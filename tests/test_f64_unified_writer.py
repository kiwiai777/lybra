"""AIPOS-F64: 单一记录写入器测试"""
import tempfile
from pathlib import Path
from tools.aipos_cli.record_writer import write_records_atomic


def test_write_single_record():
    """测试写入单条记录"""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_root = Path(tmpdir)
        
        claim_markdown = """---
record_type: claim
claim_id: claim_TEST-1_20260902_120000_agent
task_id: TEST-1
claimed_at: 2026-09-02T12:00:00Z
---
# Claim Record
"""
        
        result = write_records_atomic(
            repo_root=repo_root,
            records=[("claim", "claim_TEST-1_20260902_120000_agent", claim_markdown)],
        )
        
        assert result["ok"] is True
        assert result["wrote"] is True
        assert len(result["paths"]) == 1
        
        # 验证文件真实存在
        written_path = repo_root / result["paths"][0]
        assert written_path.exists()
        content = written_path.read_text()
        assert "record_type: claim" in content
        assert "TEST-1" in content


def test_write_atomic_multiple_records():
    """测试原子写入多条记录(publish + dispatch)"""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_root = Path(tmpdir)
        
        publish_markdown = """---
record_type: publish
publish_id: publish_AUDIT-1_20260902_120000
task_id: AUDIT-1
published_at: 2026-09-02T12:00:00Z
---
# Publish Record
"""
        
        dispatch_markdown = """---
record_type: audit_dispatch
dispatch_id: dispatch_AUDIT-1_20260902_120000
reviewed_task_id: TASK-1
audit_task_id: AUDIT-1
dispatched_at: 2026-09-02T12:00:00Z
---
# Dispatch Record
"""
        
        result = write_records_atomic(
            repo_root=repo_root,
            records=[
                ("publish", "publish_AUDIT-1_20260902_120000", publish_markdown),
                ("audit_dispatch", "dispatch_AUDIT-1_20260902_120000", dispatch_markdown),
            ],
        )
        
        assert result["ok"] is True
        assert result["wrote"] is True
        assert result["record_count"] == 2
        assert len(result["paths"]) == 2
        
        # 验证两条记录都存在
        for path_str in result["paths"]:
            path = repo_root / path_str
            assert path.exists(), f"Record not found: {path_str}"


def test_atomic_rollback_on_failure():
    """测试失败时的回滚机制"""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_root = Path(tmpdir)
        
        # 构造一个会失败的场景:无效的record_type
        try:
            write_records_atomic(
                repo_root=repo_root,
                records=[
                    ("claim", "claim_TEST-1_20260902_120000_agent", "valid markdown"),
                    ("invalid_type", "invalid_id", "should fail"),
                ],
            )
            assert False, "应该抛出异常"
        except (ValueError, RuntimeError):
            # 验证第一条记录也没有写入(原子性)
            claim_dir = repo_root / "5_tasks" / "records" / "claims" / "TEST-1"
            if claim_dir.exists():
                assert len(list(claim_dir.glob("*.md"))) == 0, "失败时应回滚所有记录"


if __name__ == "__main__":
    import sys
    try:
        test_write_single_record()
        print("✓ test_write_single_record PASS")
        
        test_write_atomic_multiple_records()
        print("✓ test_write_atomic_multiple_records PASS")
        
        test_atomic_rollback_on_failure()
        print("✓ test_atomic_rollback_on_failure PASS")
        
        print("\n所有测试通过")
    except AssertionError as e:
        print(f"✗ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
