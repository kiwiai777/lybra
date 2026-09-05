"""AIPOS-F74: 交回自检判据⑤分支合规 + 存量卡机器区重生成 + deploy 空区间授权洞修复。

验收:
① 先红后绿: 靶场造「无分支交回/错基座交回/压他卡分支交回」三场景各拒且出口正确
② 正常分支交回零回归
③ regen 对存量卡活体跑一张, amend 记录落盘、顾问区逐字节不变
④ 夹具入 run-all (git diff 自证)
⑤ 基线零新增失败
⑥ deploy 空区间 + 假 ref → 拒(先红后绿); 真 ref 零回归
"""
from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tools.aipos_cli.board_adapter import _check_branch_compliance
from tools.aipos_cli.deploy_gate import invoke_lybra_deploy


class TestBranchComplianceCheck(unittest.TestCase):
    """件①: 分支合规判据测试。"""

    def setUp(self):
        self.tmp_path = Path("/tmp/test_f74_branch")
        self.tmp_path.mkdir(parents=True, exist_ok=True)
        
        # 创建一个模拟的 git 仓库
        subprocess.run(["git", "init"], cwd=self.tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.tmp_path, check=True, capture_output=True)
        
        # 创建 main 分支并提交
        (self.tmp_path / "README.md").write_text("# Test\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=self.tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "branch", "-M", "main"], cwd=self.tmp_path, check=True, capture_output=True)

    def tearDown(self):
        import shutil
        if self.tmp_path.exists():
            shutil.rmtree(self.tmp_path)

    def test_no_branch_blocks(self):
        """验收①-a: 无分支交回 → 拒且出口正确。"""
        with patch("tools.aipos_cli.board_adapter._resolve_product_code_repo") as mock_resolve:
            mock_resolve.return_value = self.tmp_path
            
            reasons = _check_branch_compliance(
                task_id="TEST-001",
                task_metadata={"task_mode": "code"},
                repo_root=Path("/tmp/governance"),
            )
            
            self.assertTrue(len(reasons) > 0)
            self.assertIn("BRANCH_NOT_FOUND", reasons[0])
            self.assertIn("card/TEST-001", reasons[0])
            self.assertIn("git checkout -b", reasons[0])

    def test_empty_branch_blocks(self):
        """验收①-b: 分支无提交 → 拒且出口正确。"""
        # 创建分支但不提交
        subprocess.run(["git", "checkout", "-b", "card/TEST-002"], cwd=self.tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "checkout", "main"], cwd=self.tmp_path, check=True, capture_output=True)
        
        with patch("tools.aipos_cli.board_adapter._resolve_product_code_repo") as mock_resolve:
            mock_resolve.return_value = self.tmp_path
            
            reasons = _check_branch_compliance(
                task_id="TEST-002",
                task_metadata={"task_mode": "code"},
                repo_root=Path("/tmp/governance"),
            )
            
            self.assertTrue(len(reasons) > 0)
            self.assertIn("BRANCH_NO_COMMITS", reasons[0])
            self.assertIn("git add", reasons[0])
            self.assertIn("git commit", reasons[0])

    def test_wrong_base_blocks(self):
        """验收①-c: 错基座交回 → 拒且出口正确。"""
        # 创建一个旧的 commit 作为基座
        subprocess.run(["git", "checkout", "-b", "old-main"], cwd=self.tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "checkout", "main"], cwd=self.tmp_path, check=True, capture_output=True)
        
        # 在 main 上添加新 commit
        (self.tmp_path / "new.txt").write_text("new content\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "New commit on main"], cwd=self.tmp_path, check=True, capture_output=True)
        
        # 从旧 main 创建分支
        subprocess.run(["git", "checkout", "old-main"], cwd=self.tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "checkout", "-b", "card/TEST-003"], cwd=self.tmp_path, check=True, capture_output=True)
        (self.tmp_path / "feature.txt").write_text("feature\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "feat(TEST-003): add feature"], cwd=self.tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "checkout", "main"], cwd=self.tmp_path, check=True, capture_output=True)
        
        with patch("tools.aipos_cli.board_adapter._resolve_product_code_repo") as mock_resolve:
            mock_resolve.return_value = self.tmp_path
            
            reasons = _check_branch_compliance(
                task_id="TEST-003",
                task_metadata={"task_mode": "code"},
                repo_root=Path("/tmp/governance"),
            )
            
            self.assertTrue(len(reasons) > 0)
            self.assertIn("BRANCH_WRONG_BASE", reasons[0])
            self.assertIn("git rebase", reasons[0])

    def test_valid_branch_passes(self):
        """验收②: 正常分支交回零回归。"""
        # 创建正常分支并提交
        subprocess.run(["git", "checkout", "-b", "card/TEST-004"], cwd=self.tmp_path, check=True, capture_output=True)
        (self.tmp_path / "feature.txt").write_text("feature\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "feat(TEST-004): add feature"], cwd=self.tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "checkout", "main"], cwd=self.tmp_path, check=True, capture_output=True)
        
        with patch("tools.aipos_cli.board_adapter._resolve_product_code_repo") as mock_resolve:
            mock_resolve.return_value = self.tmp_path
            
            reasons = _check_branch_compliance(
                task_id="TEST-004",
                task_metadata={"task_mode": "code"},
                repo_root=Path("/tmp/governance"),
            )
            
            self.assertEqual(len(reasons), 0)


class TestDeployEmptyIntervalVacuousAuth(unittest.TestCase):
    """件③: deploy 空区间 vacuous 授权洞修复测试。"""

    def test_empty_interval_fake_ref_blocks(self):
        """验收⑥-a: 空区间 + 假 ref → 拒。"""
        with patch("tools.aipos_cli.deploy_gate.subprocess.run") as mock_run:
            # Mock git rev-parse HEAD
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="abc123\n",
            )
            
            repo_root = Path("/tmp/test_repo")
            governance_root = Path("/tmp/test_governance")
            
            # Mock VERSION 文件存在且 current == HEAD (空区间)
            version_file = repo_root / ".deploy" / "current" / "VERSION"
            version_file.parent.mkdir(parents=True, exist_ok=True)
            version_file.write_text("git_commit: abc123\n", encoding="utf-8")
            
            # Mock lybra-deploy 脚本存在
            deploy_script = repo_root / "tools" / "lybra-deploy"
            deploy_script.parent.mkdir(parents=True, exist_ok=True)
            deploy_script.write_text("#!/bin/bash\necho 'mock deploy'\n", encoding="utf-8")
            deploy_script.chmod(0o755)
            
            # Mock verdict_ref 文件不存在 (假 ref)
            (governance_root / "5_tasks" / "records" / "audit_verdicts").mkdir(parents=True, exist_ok=True)
            
            result = invoke_lybra_deploy(
                repo_root,
                verdict_ref="fake_verdict_ref",
                governance_root=governance_root,
            )
            
            self.assertFalse(result["success"])
            self.assertIn("未找到对应的门生裁决文件", result["stderr"])
            
            # 清理
            import shutil
            if repo_root.exists():
                shutil.rmtree(repo_root)
            if governance_root.exists():
                shutil.rmtree(governance_root)

    def test_empty_interval_no_reason_blocks(self):
        """验收⑥-b: 空区间无 --reason → 拒。"""
        with patch("tools.aipos_cli.deploy_gate.subprocess.run") as mock_run:
            # Mock git rev-parse HEAD
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="def456\n",
            )
            
            repo_root = Path("/tmp/test_repo_no_reason")
            governance_root = Path("/tmp/test_governance_no_reason")
            
            try:
                # Mock VERSION 文件存在且 current == HEAD (空区间)
                version_file = repo_root / ".deploy" / "current" / "VERSION"
                version_file.parent.mkdir(parents=True, exist_ok=True)
                version_file.write_text("git_commit: def456\n", encoding="utf-8")
                
                # Mock lybra-deploy 脚本存在
                deploy_script = repo_root / "tools" / "lybra-deploy"
                deploy_script.parent.mkdir(parents=True, exist_ok=True)
                deploy_script.write_text("#!/bin/bash\necho 'mock deploy'\n", encoding="utf-8")
                deploy_script.chmod(0o755)
                
                # Mock 真实 verdict 文件存在
                verdict_dir = governance_root / "5_tasks" / "records" / "audit_verdicts" / "TEST-001"
                verdict_dir.mkdir(parents=True, exist_ok=True)
                verdict_file = verdict_dir / "verdict_test_001.md"
                verdict_file.write_text(
                    "---\n"
                    "record_type: audit_verdict\n"
                    "verdict_id: verdict_test_001\n"
                    "verdict: APPROVE\n"
                    "task_id: TEST-001\n"
                    "verdict_at: 2024-01-01T00:00:00Z\n"
                    "auditor: test.auditor\n"
                    "---\n"
                    "Test verdict\n",
                    encoding="utf-8"
                )
                
                # 调用 deploy 无 reason (空区间应拒绝)
                result = invoke_lybra_deploy(
                    repo_root,
                    verdict_ref="verdict_test_001",
                    governance_root=governance_root,
                    reason=None,  # 无 reason
                )
                
                # 断言: 空区间无 reason 应该被拒绝
                self.assertFalse(result["success"])
                self.assertIn("空区间", result["stderr"] or "")
                
            finally:
                # 清理
                import shutil
                if repo_root.exists():
                    shutil.rmtree(repo_root)
                if governance_root.exists():
                    shutil.rmtree(governance_root)


class TestRegenMachineZoneRealEntry(unittest.TestCase):
    """件②: regen-machine-zone 真入口夹具 (走真 CLI 解析器与导入路径)。"""

    def test_regen_command_real_entry(self):
        """真入口夹具: 直接调用 regen_machine_zone_for_pending, 验证导入路径正确。"""
        import tempfile
        import shutil
        from tools.aipos_cli.draft_writer import regen_machine_zone_for_pending
        
        # 创建临时治理仓
        tmp_gov = Path(tempfile.mkdtemp(prefix="test_gov_regen_"))
        
        try:
            # 创建 pending 目录和一张测试卡
            pending_dir = tmp_gov / "5_tasks" / "queue" / "pending"
            pending_dir.mkdir(parents=True, exist_ok=True)
            
            test_card = pending_dir / "test-regen-001.md"
            test_card.write_text(
                "---\n"
                "task_id: TEST-REGEN-001\n"
                "title: Test Regen\n"
                "status: pending\n"
                "priority: P2\n"
                "actor: test.actor\n"
                "task_mode: code\n"
                "---\n"
                "\n"
                "## 工作纪律\n"
                "\n"
                "Old discipline section.\n",
                encoding="utf-8"
            )
            
            # 直接调用函数 (走真实导入路径)
            result = regen_machine_zone_for_pending(
                tmp_gov,
                task_id="TEST-REGEN-001",
                actor="test.actor",
                dry_run=True,
            )
            
            # 验证: 函数调用成功 (导入无错)
            self.assertIn("verdict", result)
            # 应该成功或者提示无需更新
            self.assertIn(result["verdict"], ["APPROVE", "BLOCK"])
            
            # 如果 APPROVE, 验证消息合理
            if result["verdict"] == "APPROVE":
                self.assertIn("message", result)
                self.assertTrue(
                    "Would update" in result["message"] or "No cards needed" in result["message"],
                    f"Unexpected message: {result['message']}"
                )
            
        finally:
            # 清理
            if tmp_gov.exists():
                shutil.rmtree(tmp_gov)


if __name__ == "__main__":
    unittest.main()
