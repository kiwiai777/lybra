#!/usr/bin/env python3
"""Integration test for health monitoring unhealthy detection.

Simulates a process that goes silent (no CPU activity, no file changes) and
verifies that unhealthy event is emitted after configured cycles.
"""
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

def test_unhealthy_detection():
    """Test that unhealthy is detected after sustained silence."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Path(tmpdir)
        (ws / "5_tasks" / "queue").mkdir(parents=True)
        (ws / "5_tasks" / "records").mkdir(parents=True)
        
        # Start watch with very short intervals for testing
        proc = subprocess.Popen(
            [
                sys.executable, "-m", "tools.aipos_cli.aipos_cli",
                "agent", "watch",
                "--workspace-root", str(ws),
                "--stream",
                "--health", "3",  # 3 second intervals
                "--unhealthy-cycles", "2",  # 2 cycles = 6 seconds
                "--proc-pattern", "nonexistent_process_xyz",  # Will not find any process
                "--timeout", "20",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        print("Waiting for unhealthy detection...")
        unhealthy_detected = False
        health_count = 0
        
        try:
            for _ in range(30):  # 30 * 1s = 30s max wait
                line = proc.stdout.readline()
                if not line:
                    if proc.poll() is not None:
                        break
                    time.sleep(1)
                    continue
                
                line = line.strip()
                if not line:
                    continue
                
                try:
                    event = json.loads(line)
                    print(f"Event: {event}")
                    
                    if event.get("kind") == "health":
                        health_count += 1
                        # Should show proc_alive=False (no matching process)
                        assert event["proc_alive"] is False, "Should not find nonexistent process"
                        assert event["cpu_delta"] == 0.0, "Dead process should have 0 CPU"
                    
                    elif event.get("kind") == "unhealthy":
                        unhealthy_detected = True
                        print(f"✓ Unhealthy detected after {health_count} health events")
                        assert event["reason"] in ["process_gone", "sustained_silence"]
                        assert event["silent_cycles"] >= 2
                        break
                
                except json.JSONDecodeError:
                    continue
        
        finally:
            proc.terminate()
            proc.wait(timeout=5)
        
        assert unhealthy_detected, f"Should detect unhealthy after 2 cycles (saw {health_count} health events)"
        print(f"\n✓ Test passed: unhealthy detected after {health_count} health events")
        return 0

if __name__ == "__main__":
    sys.exit(test_unhealthy_detection())
