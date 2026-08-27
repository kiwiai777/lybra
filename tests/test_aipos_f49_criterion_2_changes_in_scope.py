#!/usr/bin/env python3
"""
AIPOS-F49 判据② 夹具: git diff 文件必须落在 output_target 范围内。

测试策略: 使用 tmp_path 临时工作区，模拟 git repo + 卡面 output_target
"""
import subprocess
from pathlib import Path
import tempfile


def test_criterion_2_changes_in_scope(tmp_path: Path):
    """判据②: git diff 文件必须落在 output_target 范围内。"""
    
    # 创建临时 git repo
    repo_root = tmp_path / "test_repo"
    repo_root.mkdir()
    
    # 初始化 git
    subprocess.run(["git", "init"], cwd=repo_root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_root, check=True, capture_output=True)
    
    # 创建初始文件（在 main 分支）
    (repo_root / "tools" / "module_a").mkdir(parents=True)
    (repo_root / "tools" / "module_a" / "file.py").write_text("# Initial", encoding="utf-8")
    
    # Commit to main
    subprocess.run(["git", "add", "."], cwd=repo_root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo_root, check=True, capture_output=True)
    subprocess.run(["git", "branch", "-M", "main"], cwd=repo_root, check=True, capture_output=True)
    
    # 创建任务分支，修改范围内和范围外的文件
    subprocess.run(["git", "checkout", "-b", "card/TEST-002"], cwd=repo_root, check=True, capture_output=True)
    
    # 范围内: tools/module_a/
    (repo_root / "tools" / "module_a" / "file.py").write_text("# Modified in scope", encoding="utf-8")
    
    # 范围外: tools/module_b/
    (repo_root / "tools" / "module_b").mkdir(parents=True)
    (repo_root / "tools" / "module_b" / "other.py").write_text("# Out of scope", encoding="utf-8")
    
    subprocess.run(["git", "add", "."], cwd=repo_root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Modify files"], cwd=repo_root, check=True, capture_output=True)
    
    # 测试: 调用 _check_changes_in_scope（红测试 - 应该失败）
    from tools.aipos_cli.board_adapter import _check_changes_in_scope
    
    # Mock _resolve_product_code_repo
    import tools.aipos_cli.board_adapter as adapter_module
    original_resolve = adapter_module._resolve_product_code_repo
    adapter_module._resolve_product_code_repo = lambda x: repo_root
    
    try:
        # output_target 只包含 tools/module_a/
        reasons = _check_changes_in_scope(
            task_id="TEST-002",
            output_target="tools/module_a/(模块A文件)",
            repo_root=tmp_path,
        )
        
        # 应该有阻塞原因（tools/module_b/other.py 越界）
        assert len(reasons) > 0, "应该检测到越界文件"
        assert "CHANGES_OUT_OF_SCOPE" in reasons[0], f"应该返回 CHANGES_OUT_OF_SCOPE，实际: {reasons[0]}"
        assert "module_b" in reasons[0], f"应该提到越界文件 module_b，实际: {reasons[0]}"
        print("✓ 红测试通过: 检测到越界文件")
        
        # 绿测试: 只修改范围内文件
        subprocess.run(["git", "checkout", "main"], cwd=repo_root, check=True, capture_output=True)
        subprocess.run(["git", "checkout", "-b", "card/TEST-002-fixed"], cwd=repo_root, check=True, capture_output=True)
        (repo_root / "tools" / "module_a" / "file.py").write_text("# Modified in scope only", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=repo_root, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Modify in scope only"], cwd=repo_root, check=True, capture_output=True)
        
        reasons = _check_changes_in_scope(
            task_id="TEST-002-fixed",
            output_target="tools/module_a/(模块A文件)",
            repo_root=tmp_path,
        )
        
        # 应该没有阻塞原因
        assert len(reasons) == 0, f"只修改范围内文件不应该有阻塞，实际: {reasons}"
        print("✓ 绿测试通过: 只修改范围内文件通过")
        
    finally:
        # 恢复原函数
        adapter_module._resolve_product_code_repo = original_resolve
    
    print("✓ AIPOS-F49 判据② 测试通过")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmpdir:
        test_criterion_2_changes_in_scope(Path(tmpdir))
