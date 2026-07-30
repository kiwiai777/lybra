#!/usr/bin/env python3
"""AIPOS-276: Test suite for project-map anti-staleness machinery.

S1: Old map (含 in_flight) 兼容读 + 新推导正确
S2: 陈旧夹具触发 publish WARN 且入记录
S3: 板面红标真机可见（把 updated 改旧实测）
S4: 零回归
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from datetime import datetime, timedelta, timezone

# Add repo root to path
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from tools.aipos_cli.project_map import get_project_map
from tools.aipos_cli.draft_writer import publish_draft


def _write_map(repo_root: Path, content: str):
    map_path = repo_root / "governance" / "project-map.md"
    map_path.parent.mkdir(parents=True, exist_ok=True)
    map_path.write_text(content, encoding="utf-8")


def _write_return_record(repo_root: Path, task_id: str, returned_at: str):
    """Helper: write a minimal return record."""
    record_path = repo_root / "5_tasks" / "records" / "returns" / task_id / f"return_{task_id}.md"
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(f"""---
record_type: return_record
task_id: {task_id}
return_id: return_{task_id}
returned_at: {returned_at}
created_at: {returned_at}
---
# Return
""", encoding="utf-8")


def _write_draft(repo_root: Path, task_id: str) -> Path:
    """Helper: write a minimal valid draft."""
    draft_path = repo_root / "5_tasks" / "drafts" / f"{task_id.lower()}.md"
    draft_path.parent.mkdir(parents=True, exist_ok=True)
    draft_path.write_text(f"""---
task_id: {task_id}
title: Test Task
project: lybra
assigned_to: test-agent
context_bundle: test
task_mode: code
priority: low
status: pending
created_by: test
needs_owner: false
output_target: test/
artifact_policy: formal_write
---
# Test
""", encoding="utf-8")
    return draft_path


def test_s1_old_map_compat():
    """S1: Old map (含 in_flight) 兼容读 + warnings + in_flight 返回空。"""
    print("\n=== S1: Old map compatibility ===")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = Path(tmpdir)
        (repo / "5_tasks" / "queue").mkdir(parents=True, exist_ok=True)
        
        # Write old-style map with in_flight (proper YAML indentation)
        _write_map(repo, """---
map_version: 1
updated: 2026-01-15
current: M1 - Foundation
in_flight:
  - TASK-100
  - TASK-101
next:
  - TASK-200
horizon:
  - TASK-300
milestones:
  - id: M1
    title: Foundation
    refs: []
---
# Project Map
""")
        
        result = get_project_map(repo_root=repo)
        
        # Check warnings about deprecated in_flight
        assert result["verdict"] == "WARN", f"Expected WARN verdict, got {result['verdict']}"
        warnings = result.get("warnings", [])
        assert any("in_flight" in w and "deprecated" in w for w in warnings), \
            f"Expected in_flight deprecation warning, got: {warnings}"
        
        # Check in_flight returned as empty
        data = result.get("data", {})
        assert data.get("in_flight") == [], f"Expected empty in_flight, got {data.get('in_flight')}"
        
        # Check other fields preserved
        assert data.get("current") == "M1 - Foundation"
        assert data.get("next") == ["TASK-200"]
        assert data.get("horizon") == ["TASK-300"]
        
        print("✓ S1 PASS: Old map read with warning, in_flight ignored")


def test_s2_stale_map_publish_warn():
    """S2: 陈旧地图触发 publish WARN (updated 早于最近 return >3天)。"""
    print("\n=== S2: Stale map publish warning ===")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = Path(tmpdir)
        (repo / "5_tasks" / "queue").mkdir(parents=True, exist_ok=True)
        
        # Write map with old updated date
        old_date = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%d")
        _write_map(repo, f"""---
map_version: 1
updated: {old_date}
current: M1
milestones: []
---
# Map
""")
        
        # Write a recent return record (3+ days after map update)
        recent_date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        _write_return_record(repo, "TEST-STALE", recent_date)
        
        # Create and try to publish a draft
        draft_path = _write_draft(repo, "TEST-PUB")
        result = publish_draft(repo, draft_path, dry_run=True)
        
        # Check for staleness warning
        warnings = result.get("warnings", [])
        stale_warn = any("PROJECT_MAP_STALE" in w or "地图更新于" in w for w in warnings)
        
        assert stale_warn, f"Expected PROJECT_MAP_STALE warning, got warnings: {warnings}"
        assert result["verdict"] in ["WARN", "PASS"], f"Unexpected verdict: {result['verdict']}"
        
        print("✓ S2 PASS: Stale map triggers publish warning")


def test_s3_no_map_graceful():
    """S3: 无地图 / 无 updated 字段优雅降级 (不 WARN 不红标)。"""
    print("\n=== S3: No map graceful degradation ===")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = Path(tmpdir)
        (repo / "5_tasks" / "queue").mkdir(parents=True, exist_ok=True)
        
        # No map file
        result1 = get_project_map(repo_root=repo)
        assert not result1.get("data", {}).get("available"), "Expected unavailable when no map"
        
        # Map without updated field
        _write_map(repo, """---
map_version: 1
current: M1
milestones: []
---
# Map
""")
        
        result2 = get_project_map(repo_root=repo)
        data = result2.get("data", {})
        assert data.get("available"), "Expected available with map"
        assert data.get("updated") == "", "Expected empty updated field"
        
        # Publish with no map updated should not warn
        draft_path = _write_draft(repo, "TEST-NOMAP")
        result3 = publish_draft(repo, draft_path, dry_run=True)
        warnings = result3.get("warnings", [])
        stale_warn = any("PROJECT_MAP_STALE" in w for w in warnings)
        assert not stale_warn, f"Should not warn when map has no updated, got: {warnings}"
        
        print("✓ S3 PASS: No map / no updated field graceful degradation")


def test_s4_fresh_map_no_warn():
    """S4: 新鲜地图不触发警告 (updated 在最近 return 之后或 <3天差距)。"""
    print("\n=== S4: Fresh map no warning ===")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = Path(tmpdir)
        (repo / "5_tasks" / "queue").mkdir(parents=True, exist_ok=True)
        
        # Write return record
        old_return = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        _write_return_record(repo, "TEST-OLD", old_return)
        
        # Write fresh map (updated yesterday)
        fresh_date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        _write_map(repo, f"""---
map_version: 1
updated: {fresh_date}
current: M1
milestones: []
---
# Map
""")
        
        # Publish should not warn
        draft_path = _write_draft(repo, "TEST-FRESH")
        result = publish_draft(repo, draft_path, dry_run=True)
        
        warnings = result.get("warnings", [])
        stale_warn = any("PROJECT_MAP_STALE" in w for w in warnings)
        assert not stale_warn, f"Fresh map should not warn, got: {warnings}"
        
        print("✓ S4 PASS: Fresh map does not trigger warning")


def main():
    """Run all tests."""
    print("AIPOS-276 Test Suite")
    print("=" * 60)
    
    try:
        test_s1_old_map_compat()
        test_s2_stale_map_publish_warn()
        test_s3_no_map_graceful()
        test_s4_fresh_map_no_warn()
        
        print("\n" + "=" * 60)
        print("✅ All tests PASSED")
        return 0
        
    except AssertionError as e:
        print(f"\n❌ Test FAILED: {e}")
        return 1
    except Exception as e:
        print(f"\n💥 Test ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    sys.exit(main())
