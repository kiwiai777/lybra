#!/usr/bin/env python3
"""FIX-1 测试:验证 warnings 写入 publish 记录文件。

F-276-1: render_publish_record() 增 warnings 参数，调用处传递 validation["warnings"]，
         staleness warning 写入 publish 记录 frontmatter。
"""
from __future__ import annotations

import sys
import tempfile
import yaml
from pathlib import Path
from datetime import datetime, timedelta, timezone

# Add repo root to path
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from tools.aipos_cli.draft_writer import publish_draft


def _write_map(repo_root: Path, updated_date: str):
    """Write project-map.md with given updated date."""
    map_path = repo_root / "governance" / "project-map.md"
    map_path.parent.mkdir(parents=True, exist_ok=True)
    map_path.write_text(f"""---
map_version: 1
updated: {updated_date}
current: M1
milestones: []
---
# Project Map
""", encoding="utf-8")


def _write_return_record(repo_root: Path, task_id: str, returned_at: str):
    """Write a minimal return record."""
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
    """Write a minimal valid draft."""
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
# Test Task
## Goal
Test content
""", encoding="utf-8")
    return draft_path


def test_f276_1_warnings_in_publish_record():
    """F-276-1: 验证 staleness warning 写入 publish 记录文件。"""
    print("\n=== F-276-1: Warnings in publish record ===")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = Path(tmpdir)
        (repo / "5_tasks" / "queue").mkdir(parents=True, exist_ok=True)
        
        # Setup: 陈旧地图（14 天前）+ 最近 return（1 天前）
        stale_date = (datetime.now(timezone.utc) - timedelta(days=14)).strftime("%Y-%m-%d")
        recent_return = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        
        _write_map(repo, stale_date)
        _write_return_record(repo, "TEST-RETURN", recent_return)
        
        # Create and publish draft (dry_run=False to actually write record)
        draft_path = _write_draft(repo, "TEST-PUB-276")
        result = publish_draft(repo, draft_path, dry_run=False)
        
        # 1. Check dry_run response contains warning
        assert result["verdict"] == "WARN", f"Expected WARN verdict, got {result['verdict']}"
        warnings = result.get("warnings", [])
        stale_warn = any("PROJECT_MAP_STALE" in w or "地图更新于" in w for w in warnings)
        assert stale_warn, f"Expected PROJECT_MAP_STALE warning in response, got: {warnings}"
        print(f"  ✓ Publish response contains staleness warning: {[w for w in warnings if 'MAP_STALE' in w or '地图' in w]}")
        
        # 2. Check publish record file exists
        assert result["wrote"], "Expected publish to write files"
        record_path_rel = result.get("publish_record_path")
        assert record_path_rel, f"No publish_record_path in result: {result}"
        
        record_path = repo / record_path_rel
        assert record_path.exists(), f"Publish record not found at {record_path}"
        print(f"  ✓ Publish record file exists: {record_path_rel}")
        
        # 3. Parse record file and check warnings in frontmatter
        record_content = record_path.read_text(encoding="utf-8")
        
        # Split frontmatter and body
        parts = record_content.split("---\n", 2)
        assert len(parts) >= 3, f"Invalid record format: {record_content[:200]}"
        
        frontmatter = yaml.safe_load(parts[1])
        assert frontmatter, "Failed to parse frontmatter"
        print(f"  ✓ Frontmatter parsed successfully")
        
        # 4. Check warnings field in frontmatter
        record_warnings = frontmatter.get("warnings")
        assert record_warnings is not None, f"No 'warnings' field in frontmatter: {frontmatter.keys()}"
        assert isinstance(record_warnings, list), f"warnings should be list, got {type(record_warnings)}"
        
        stale_in_record = any("PROJECT_MAP_STALE" in w or "地图更新于" in w for w in record_warnings)
        assert stale_in_record, f"Expected PROJECT_MAP_STALE in record warnings, got: {record_warnings}"
        
        print(f"  ✓ Publish record contains warnings field: {record_warnings}")
        print(f"  ✓ Staleness warning persisted to record file")
        
        print("\n✅ F-276-1 PASS: Warnings successfully written to publish record")
        return True


def main():
    """Run F-276-1 test."""
    print("FIX-1 Test Suite: F-276-1 验证")
    print("=" * 60)
    
    try:
        test_f276_1_warnings_in_publish_record()
        print("\n" + "=" * 60)
        print("✅ F-276-1 修复验证通过")
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
