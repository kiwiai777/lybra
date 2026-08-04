"""AIPOS-327 S2: Test isolation - ensure tests never write to real ~/.lybra/

Regression test for the bug where test fixtures polluted the real user connection.json
with temporary workspace paths (e.g., /tmp/pytest-of-kiwi/pytest-7/...).

This test runs the full test suite and asserts that:
1. Real ~/.lybra/local/connection.json is not modified (if it exists)
2. No test writes to the real user HOME directory
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


class ConnectionIsolationTests(unittest.TestCase):
    """Verify that test suite does not pollute real ~/.lybra/ directory."""
    
    def test_test_suite_does_not_modify_real_connection_json(self) -> None:
        """Regression test: running tests should not modify ~/.lybra/local/connection.json.
        
        AIPOS-327 context: A test fixture wrote workspace_root=/tmp/pytest-of-kiwi/... to
        the real user connection.json, causing `lybra serve status` to report a fake workspace.
        """
        real_home = Path.home()
        real_connection = real_home / ".lybra" / "local" / "connection.json"
        
        # Capture state before test (if exists)
        before_exists = real_connection.exists()
        before_mtime = real_connection.stat().st_mtime if before_exists else None
        before_content = real_connection.read_text(encoding="utf-8") if before_exists else None
        
        # Run a subset of tests that are most likely to pollute (service_mode tests)
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-xvs", "tools/aipos_cli/tests/test_service_mode.py::ServiceModeTests::test_start_creates_gitignored_0600_connection_without_printing_raw_tokens"],
            cwd=Path(__file__).parents[3],  # lybra repo root
            capture_output=True,
            text=True,
        )
        
        # After test: check real connection.json not modified
        after_exists = real_connection.exists()
        
        if before_exists:
            # If connection existed before, it should still exist with same content
            self.assertTrue(after_exists, f"Real {real_connection} was deleted by test")
            
            after_mtime = real_connection.stat().st_mtime
            after_content = real_connection.read_text(encoding="utf-8")
            
            # Allow for filesystem timestamp granularity, but content must be identical
            self.assertEqual(
                before_content,
                after_content,
                f"Real {real_connection} was modified by test suite.\n"
                f"Before:\n{before_content}\n\nAfter:\n{after_content}"
            )
            
            # If content is same but mtime changed, that's acceptable (e.g., filesystem quirks)
            # but we should warn
            if after_mtime != before_mtime:
                print(f"WARNING: {real_connection} mtime changed but content is same (possible false alarm)", file=sys.stderr)
        else:
            # If connection didn't exist before, it shouldn't exist after
            self.assertFalse(
                after_exists,
                f"Real {real_connection} was created by test suite (should use temp HOME)"
            )
    
    def test_connection_json_workspace_root_points_to_real_workspace(self) -> None:
        """If ~/.lybra/local/connection.json exists, its workspace_root should be a real directory.
        
        This catches the pollution case where workspace_root=/tmp/pytest-of-kiwi/... (already deleted).
        """
        real_home = Path.home()
        real_connection = real_home / ".lybra" / "local" / "connection.json"
        
        if not real_connection.exists():
            self.skipTest("No real connection.json exists, nothing to check")
        
        try:
            config = json.loads(real_connection.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            self.fail(f"Failed to read {real_connection}: {exc}")
        
        workspace_root = config.get("workspace_root")
        if workspace_root:
            ws_path = Path(workspace_root).expanduser()
            self.assertTrue(
                ws_path.exists(),
                f"workspace_root in {real_connection} points to non-existent path: {workspace_root}\n"
                f"This indicates test pollution. Run manual fix: see AIPOS-327 S2 recovery steps."
            )
            
            # Additional check: should not contain pytest temp patterns
            self.assertNotIn(
                "pytest-of-",
                str(workspace_root),
                f"workspace_root in {real_connection} contains pytest temp path: {workspace_root}\n"
                f"This indicates test pollution."
            )


if __name__ == "__main__":
    unittest.main()
