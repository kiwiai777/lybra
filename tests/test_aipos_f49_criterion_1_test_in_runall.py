#!/usr/bin/env python3
"""
AIPOS-F49 判据① 夹具: 本卡新增 test 文件必须在 run-all 清单中。

测试策略: 使用 tmp_path 临时工作区，模拟 git repo + run-all.sh
"""
import subprocess
from pathlib import Path
import tempfile
import shutil


def test_criterion_1_test_in_runall(tmp_path: Path):
    """判据①: 本卡新增 test 文件必须在 run-all.sh 清单中。"""
    
    # 创建临时 git repo
    repo_root = tmp_path / "test_repo"
    repo_root.mkdir()
    
    # 初始化 git
    subprocess.run(["git", "init"], cwd=repo_root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_root, check=True, capture_output=True)
    
    # 创建 run-all.sh（在 main 分支）
    runall_dir = repo_root / "agents" / "harness" / "pi" / "lybra-loop" / "tests"
    runall_dir.mkdir(parents=True)
    runall_path = runall_dir / "run-all.sh"
    runall_path.write_text("""#!/bin/bash
# Existing tests
python3 tests/test_existing_feature.py
""", encoding="utf-8")
    
    # Commit to master (default branch), then create main
    subprocess.run(["git", "add", "."], cwd=repo_root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo_root, check=True, capture_output=True)
    subprocess.run(["git", "branch", "-M", "main"], cwd=repo_root, check=True, capture_output=True)
    
    # 创建任务分支，添加新 test 文件
    subprocess.run(["git", "checkout", "-b", "card/TEST-001"], cwd=repo_root, check=True, capture_output=True)
    test_file = repo_root / "tests" / "test_new_feature.py"
    test_file.parent.mkdir(exist_ok=True)
    test_file.write_text("def test_new(): pass", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo_root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Add new test"], cwd=repo_root, check=True, capture_output=True)
    
    # 测试: 调用 _check_test_in_runall（红测试 - 应该失败）
    from tools.aipos_cli.board_adapter import _check_test_in_runall
    
    # Mock _resolve_product_code_repo
    import tools.aipos_cli.board_adapter as adapter_module
    original_resolve = adapter_module._resolve_product_code_repo
    adapter_module._resolve_product_code_repo = lambda x: repo_root
    
    try:
        reasons = _check_test_in_runall(
            task_id="TEST-001",
            repo_root=tmp_path,  # 任意路径，会被 mock 替换
        )
        
        # 应该有阻塞原因（test_new_feature.py 不在 run-all.sh 中）
        assert len(reasons) > 0, "应该检测到 test 文件未加入 run-all.sh"
        assert "TEST_NOT_IN_RUNALL" in reasons[0], f"应该返回 TEST_NOT_IN_RUNALL，实际: {reasons[0]}"
        assert "test_new_feature.py" in reasons[0], f"应该提到缺失的测试文件，实际: {reasons[0]}"
        print("✓ 红测试通过: 检测到 test 文件未加入 run-all.sh")
        
        # 绿测试: 将 test 加入 run-all.sh
        subprocess.run(["git", "checkout", "card/TEST-001"], cwd=repo_root, check=True, capture_output=True)
        runall_path.write_text("""#!/bin/bash
# Existing tests
python3 tests/test_existing_feature.py
python3 tests/test_new_feature.py
""", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=repo_root, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add to run-all"], cwd=repo_root, check=True, capture_output=True)
        
        reasons = _check_test_in_runall(
            task_id="TEST-001",
            repo_root=tmp_path,
        )
        
        # 应该没有阻塞原因
        assert len(reasons) == 0, f"加入 run-all 后不应该有阻塞，实际: {reasons}"
        print("✓ 绿测试通过: test 文件加入 run-all.sh 后通过")
        
    finally:
        # 恢复原函数
        adapter_module._resolve_product_code_repo = original_resolve
    
    print("✓ AIPOS-F49 判据① 测试通过")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmpdir:
        test_criterion_1_test_in_runall(Path(tmpdir))
