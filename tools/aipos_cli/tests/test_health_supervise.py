"""AIPOS-295 — Tests for `agent watch --health` monitoring (supervise 部分随 AIPOS-F91 退役删除).

Test coverage (S5):
- T1: Health event emission (proc_alive, cpu_delta, session_files, worktree_changes)
- T2: Unhealthy detection (process gone)
- T3: Unhealthy detection (sustained silence: cpu_delta≈0 + no files + no worktree)
- T4: Respawn event (first failure)
- T5: Escalate event (second failure)
- T6: Process tree detection (pi subprocess, not timeout wrapper)
- T7: CLI argument validation (--health requires --stream)
- T8: Integration: full supervise cycle with simulated failure
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add tools directory to path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.aipos_cli.agent_watch_fs import (
    _count_new_session_files,
    _count_worktree_changes,
    _find_pi_processes,
    _get_process_cpu_time,
)


class TestHealthMonitoring(unittest.TestCase):
    """Test health monitoring functions (AIPOS-295 S1)."""
    
    def test_find_pi_processes_with_pid_file(self):
        """T6: Process tree detection excludes timeout wrapper."""
        # Create a temporary PID file
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.pid') as f:
            # Use current process PID (we know it exists)
            f.write(str(os.getpid()))
            pid_file = f.name
        
        try:
            # This should find our process or children (if any)
            pids = _find_pi_processes(pid_file=pid_file)
            # We can't assert exact count since it depends on environment
            # but it should return a list
            self.assertIsInstance(pids, list)
        finally:
            os.unlink(pid_file)
    
    def test_find_pi_processes_with_pattern(self):
        """T6: Process pattern matching."""
        # Find python processes (we know at least this test is running)
        pids = _find_pi_processes(proc_pattern='python')
        self.assertIsInstance(pids, list)
        # Should find at least our test process
        self.assertGreaterEqual(len(pids), 1)
    
    def test_get_process_cpu_time(self):
        """T1: CPU time measurement."""
        # Get CPU time for current process
        cpu_time = _get_process_cpu_time([os.getpid()])
        self.assertIsInstance(cpu_time, float)
        self.assertGreaterEqual(cpu_time, 0.0)
    
    def test_count_new_session_files(self):
        """T1: Session file counting."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create some files
            Path(tmpdir, "file1.txt").write_text("test")
            time.sleep(0.1)
            timestamp = time.time()
            time.sleep(0.1)
            Path(tmpdir, "file2.txt").write_text("test")
            
            # Should find 1 new file after timestamp
            count = _count_new_session_files([tmpdir], timestamp)
            self.assertEqual(count, 1)
    
    def test_count_worktree_changes(self):
        """T1: Worktree change counting."""
        # Test with non-git directory (should return 0)
        with tempfile.TemporaryDirectory() as tmpdir:
            count = _count_worktree_changes(tmpdir, time.time())
            self.assertEqual(count, 0)


class TestHealthEvents(unittest.TestCase):
    """Test health event emission in watch --stream --health mode."""
    
    def test_health_requires_stream(self):
        """T7: --health requires --stream mode."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create minimal workspace structure
            ws = Path(tmpdir)
            (ws / "5_tasks" / "queue").mkdir(parents=True)
            (ws / "5_tasks" / "records").mkdir(parents=True)
            
            # Run watch with --health but no --stream (should fail)
            result = subprocess.run(
                [
                    sys.executable, "-m", "tools.aipos_cli.aipos_cli",
                    "agent", "watch",
                    "--workspace-root", str(ws),
                    "--health", "10",
                ],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--health requires --stream", result.stderr)
    
    def test_health_event_structure(self):
        """T1: Health event contains required fields."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)
            (ws / "5_tasks" / "queue").mkdir(parents=True)
            (ws / "5_tasks" / "records").mkdir(parents=True)
            
            # Run watch with --stream --health (short interval for testing)
            proc = subprocess.Popen(
                [
                    sys.executable, "-m", "tools.aipos_cli.aipos_cli",
                    "agent", "watch",
                    "--workspace-root", str(ws),
                    "--stream",
                    "--health", "2",
                    "--timeout", "10",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            
            try:
                # Wait for at least one health event
                health_event = None
                for _ in range(20):  # 20 * 0.5s = 10s max wait
                    line = proc.stdout.readline()
                    if not line:
                        time.sleep(0.5)
                        continue
                    
                    try:
                        event = json.loads(line.strip())
                        if event.get("kind") == "health":
                            health_event = event
                            break
                    except json.JSONDecodeError:
                        continue
                
                self.assertIsNotNone(health_event, "Should receive at least one health event")
                
                # Verify health event structure (S1)
                self.assertEqual(health_event["kind"], "health")
                self.assertIn("proc_alive", health_event)
                self.assertIn("cpu_delta", health_event)
                self.assertIn("new_session_files", health_event)
                self.assertIn("worktree_changes", health_event)
                self.assertIn("silent_secs", health_event)
                
                # Types
                self.assertIsInstance(health_event["proc_alive"], bool)
                self.assertIsInstance(health_event["cpu_delta"], (int, float))
                self.assertIsInstance(health_event["new_session_files"], int)
                self.assertIsInstance(health_event["worktree_changes"], int)
                self.assertIsInstance(health_event["silent_secs"], int)
            finally:
                proc.terminate()
                proc.wait(timeout=5)


# TestSupervise(agent supervise CLI + ESCALATE 文件)随 agent_supervise 模块 AIPOS-F91 退役删除。


def run_tests():
    """Run all tests."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    # Add all test classes
    suite.addTests(loader.loadTestsFromTestCase(TestHealthMonitoring))
    suite.addTests(loader.loadTestsFromTestCase(TestHealthEvents))
    
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(run_tests())
