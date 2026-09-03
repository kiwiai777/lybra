#!/usr/bin/env python3
"""AIPOS-F65C 件①: 坏 frontmatter 卡修复通路测试。

验收:F42 活体经该通路被隔离或修复, 全程经门。
"""
import subprocess
import sys
import tempfile
from pathlib import Path


def test_repair_bad_frontmatter_with_double_star():
    """修复未引号的 ** (YAML 别名语法错误)"""
    # 创建临时测试卡
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_root = Path(tmpdir)
        queue_dir = repo_root / "5_tasks" / "queue" / "pending"
        queue_dir.mkdir(parents=True)
        
        task_file = queue_dir / "test-bad-yaml.md"
        bad_yaml_content = """---
task_id: TEST-BAD-YAML
title: Test bad YAML
project: test
status: pending
result_summary: **这是未引号的加粗文本会导致 YAML 解析失败**
governance_refs:
- '正常的列表项'
- 另一个未引号的 ** 会失败
---
# Test Task

This is a test task with bad frontmatter.
"""
        task_file.write_text(bad_yaml_content, encoding="utf-8")
        
        # 运行修复(dry-run)
        result = subprocess.run(
            [sys.executable, "-m", "tools.aipos_cli.aipos_cli", 
             "queue", "repair",
             "--task-id", "TEST-BAD-YAML",
             "--actor", "test-repair",
             "--dry-run",
             "--json"],
            capture_output=True,
            text=True,
            cwd=str(repo_root)
        )
        
        assert result.returncode == 0, f"repair dry-run 应成功, stderr: {result.stderr}"
        
        import json
        output = json.loads(result.stdout)
        assert output["verdict"] == "OK", "修复应返回 OK"
        assert len(output["repairs_made"]) > 0, "应检测到需要修复的项"
        print(f"  Detected {len(output['repairs_made'])} repairs needed")


def test_repair_writes_amendment_record():
    """修复应记录 amendment"""
    # 创建临时测试卡
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_root = Path(tmpdir)
        queue_dir = repo_root / "5_tasks" / "queue" / "pending"
        queue_dir.mkdir(parents=True)
        
        task_file = queue_dir / "test-repair-record.md"
        bad_yaml_content = """---
task_id: TEST-REPAIR-RECORD
title: Test repair record
project: test
status: pending
summary: **Bad YAML**
---
# Test
"""
        task_file.write_text(bad_yaml_content, encoding="utf-8")
        
        # 运行修复(实际写入)
        result = subprocess.run(
            [sys.executable, "-m", "tools.aipos_cli.aipos_cli", 
             "queue", "repair",
             "--task-id", "TEST-REPAIR-RECORD",
             "--actor", "test-repair",
             "--json"],
            capture_output=True,
            text=True,
            cwd=str(repo_root)
        )
        
        assert result.returncode == 0, f"repair 应成功, stderr: {result.stderr}"
        
        import json
        output = json.loads(result.stdout)
        assert output["verdict"] == "OK", "修复应返回 OK"
        assert "file_written" in output, "应写入文件"
        assert "amendment_note" in output, "应有 amendment 记录"
        
        # 验证文件已修复
        repaired_content = task_file.read_text(encoding="utf-8")
        assert "summary: '**Bad YAML**'" in repaired_content, "应给 ** 值加引号"
        print(f"  Amendment note: {output['amendment_note']}")


def test_no_repair_needed_for_valid_yaml():
    """有效的 YAML 无需修复"""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_root = Path(tmpdir)
        queue_dir = repo_root / "5_tasks" / "queue" / "pending"
        queue_dir.mkdir(parents=True)
        
        task_file = queue_dir / "test-valid-yaml.md"
        valid_yaml_content = """---
task_id: TEST-VALID-YAML
title: Test valid YAML
project: test
status: pending
summary: 'Properly quoted **bold** text'
---
# Test
"""
        task_file.write_text(valid_yaml_content, encoding="utf-8")
        
        # 运行修复
        result = subprocess.run(
            [sys.executable, "-m", "tools.aipos_cli.aipos_cli", 
             "queue", "repair",
             "--task-id", "TEST-VALID-YAML",
             "--actor", "test-repair",
             "--dry-run",
             "--json"],
            capture_output=True,
            text=True,
            cwd=str(repo_root)
        )
        
        assert result.returncode == 0, f"repair 应成功, stderr: {result.stderr}"
        
        import json
        output = json.loads(result.stdout)
        assert output["verdict"] == "OK", "修复应返回 OK"
        assert len(output["repairs_made"]) == 0, "有效 YAML 无需修复"
        assert "message" in output, "应说明无需修复"


def test_repair_task_not_found():
    """任务不存在:应报错"""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_root = Path(tmpdir)
        queue_dir = repo_root / "5_tasks" / "queue" / "pending"
        queue_dir.mkdir(parents=True)
        
        # 运行修复(任务不存在)
        result = subprocess.run(
            [sys.executable, "-m", "tools.aipos_cli.aipos_cli", 
             "queue", "repair",
             "--task-id", "NOT-EXISTS",
             "--actor", "test-repair",
             "--dry-run",
             "--json"],
            capture_output=True,
            text=True,
            cwd=str(repo_root)
        )
        
        assert result.returncode != 0, "任务不存在应返回错误"
        
        import json
        output = json.loads(result.stdout)
        assert output["verdict"] == "BLOCK", "任务不存在应 BLOCK"
        assert "blocking_reasons" in output, "应有阻塞原因"


if __name__ == "__main__":
    print("Running AIPOS-F65C 件① frontmatter repair tests...")
    
    test_repair_bad_frontmatter_with_double_star()
    print("✓ test_repair_bad_frontmatter_with_double_star")
    
    test_repair_writes_amendment_record()
    print("✓ test_repair_writes_amendment_record")
    
    test_no_repair_needed_for_valid_yaml()
    print("✓ test_no_repair_needed_for_valid_yaml")
    
    test_repair_task_not_found()
    print("✓ test_repair_task_not_found")
    
    print("\n✅ All frontmatter repair tests passed!")
