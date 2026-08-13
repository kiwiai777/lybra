#!/usr/bin/env python3
"""
AIPOS-R6A F-003 回归测试: CLI return BLOCK 时必须零副作用
验证: BLOCK verdict 返回时，任务卡文件保持不变
"""
import sys
import subprocess
from pathlib import Path
import hashlib

def compute_file_hash(path: Path) -> str:
    """计算文件SHA256哈希"""
    return hashlib.sha256(path.read_bytes()).hexdigest()

def test_cli_return_block_atomicity():
    """测试CLI return在BLOCK时不修改文件"""
    repo_root = Path("/home/kiwi/ai-project-os/2_projects/lybra")
    task_file = repo_root / "5_tasks/queue/claimed/aipos-r6a.md"
    
    if not task_file.exists():
        print(f"❌ SKIP: Task file not found: {task_file}")
        return 2
    
    # 记录修改前的哈希
    hash_before = compute_file_hash(task_file)
    
    # 尝试用错误的actor执行return (应该BLOCK)
    cmd = [
        "lybra", "queue", "return",
        "--task-id", "AIPOS-R6A",
        "--actor", "wrong.actor.test",
        "--agent-instance", "wrong.actor.test",
        "--owner-policy-ref", "pol_test",
        "--result-summary", "test block atomicity",
    ]
    
    result = subprocess.run(
        cmd,
        cwd=str(repo_root),
        capture_output=True,
        text=True,
    )
    
    # 检查返回值 (应该非0，表示BLOCK)
    if result.returncode == 0:
        print(f"❌ FAIL: Command succeeded but should have blocked")
        print(f"stdout: {result.stdout}")
        return 1
    
    # 检查输出包含BLOCK
    if "BLOCK" not in result.stdout:
        print(f"❌ FAIL: Output does not contain BLOCK verdict")
        print(f"stdout: {result.stdout}")
        return 1
    
    # 记录修改后的哈希
    hash_after = compute_file_hash(task_file)
    
    # 验证文件未被修改
    if hash_before != hash_after:
        print(f"❌ FAIL: File was modified despite BLOCK verdict")
        print(f"  Hash before: {hash_before}")
        print(f"  Hash after:  {hash_after}")
        return 1
    
    print(f"✅ PASS: CLI return BLOCK with zero side effects")
    print(f"  Command blocked: {result.returncode != 0}")
    print(f"  File unchanged: {hash_before == hash_after}")
    return 0

if __name__ == "__main__":
    sys.exit(test_cli_return_block_atomicity())
