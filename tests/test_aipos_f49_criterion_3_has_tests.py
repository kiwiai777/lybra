#!/usr/bin/env python3
"""
AIPOS-F49 判据③ 夹具: code 类卡必须有新增/修改的 test 文件。

测试策略: 使用 tmp_path 临时工作区，模拟 git repo + code 类卡
"""
import subprocess
from pathlib import Path
import tempfile


def test_criterion_3_has_tests(tmp_path: Path):
    """判据③: code 类卡必须有新增/修改的 test 文件。"""
    
    # 创建临时 git repo
    repo_root = tmp_path / "test_repo"
    repo_root.mkdir()
    
    # 初始化 git
    subprocess.run(["git", "init"], cwd=repo_root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_root, check=True, capture_output=True)
    
    # 创建初始文件（在 main 分支）
    (repo_root / "src").mkdir(parents=True)
    (repo_root / "src" / "module.py").write_text("# Initial", encoding="utf-8")
    
    # Commit to main
    subprocess.run(["git", "add", "."], cwd=repo_root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo_root, check=True, capture_output=True)
    subprocess.run(["git", "branch", "-M", "main"], cwd=repo_root, check=True, capture_output=True)
    
    # 红测试: 创建任务分支，只修改代码文件，不添加测试
    subprocess.run(["git", "checkout", "-b", "card/TEST-003"], cwd=repo_root, check=True, capture_output=True)
    (repo_root / "src" / "module.py").write_text("# Modified without tests", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo_root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Modify code only"], cwd=repo_root, check=True, capture_output=True)
    
    # 测试: 调用 _check_has_tests（红测试 - 应该失败）
    from tools.aipos_cli.board_adapter import _check_has_tests
    
    # Mock _resolve_product_code_repo
    import tools.aipos_cli.board_adapter as adapter_module
    original_resolve = adapter_module._resolve_product_code_repo
    adapter_module._resolve_product_code_repo = lambda x: repo_root
    
    try:
        reasons = _check_has_tests(
            task_id="TEST-003",
            repo_root=tmp_path,
        )
        
        # 应该有阻塞原因（无测试文件）
        assert len(reasons) > 0, "应该检测到缺少测试文件"
        assert "NO_TESTS" in reasons[0], f"应该返回 NO_TESTS，实际: {reasons[0]}"
        print("✓ 红测试通过: 检测到缺少测试文件")
        
        # 绿测试: 添加测试文件
        subprocess.run(["git", "checkout", "main"], cwd=repo_root, check=True, capture_output=True)
        subprocess.run(["git", "checkout", "-b", "card/TEST-003-fixed"], cwd=repo_root, check=True, capture_output=True)
        (repo_root / "src" / "module.py").write_text("# Modified with tests", encoding="utf-8")
        (repo_root / "tests").mkdir(parents=True)
        (repo_root / "tests" / "test_module.py").write_text("def test_module(): pass", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=repo_root, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add tests"], cwd=repo_root, check=True, capture_output=True)
        
        reasons = _check_has_tests(
            task_id="TEST-003-fixed",
            repo_root=tmp_path,
        )
        
        # 应该没有阻塞原因
        assert len(reasons) == 0, f"添加测试后不应该有阻塞，实际: {reasons}"
        print("✓ 绿测试通过: 添加测试文件后通过")
        
    finally:
        # 恢复原函数
        adapter_module._resolve_product_code_repo = original_resolve
    
    print("✓ AIPOS-F49 判据③ 测试通过")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmpdir:
        test_criterion_3_has_tests(Path(tmpdir))
