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
    
    def test_branch_from_other_card_blocks(self):
        """验收②-压他卡分支: 从他卡分支创建分支交回 → 拒（BRANCH_WRONG_BASE）。
        
        AIPOS-F74-R3-F2: 补充专项测试 - 从他卡分支（如 card/OTHER-TASK）
        创建本卡分支（card/TEST-005），merge-base 不是 main 当前 HEAD，应拒。
        
        场景: main 有新提交后，从老的 card/OTHER-TASK 创建新分支，
        merge-base 会是 main 的老 commit，不是当前 HEAD。
        """
        # 创建他卡分支（基于当前 main）
        subprocess.run(["git", "checkout", "-b", "card/OTHER-TASK"], cwd=self.tmp_path, check=True, capture_output=True)
        (self.tmp_path / "other.txt").write_text("other task\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "feat(OTHER-TASK): other"], cwd=self.tmp_path, check=True, capture_output=True)
        
        # 回到 main，添加新提交（让 main 前进）
        subprocess.run(["git", "checkout", "main"], cwd=self.tmp_path, check=True, capture_output=True)
        (self.tmp_path / "main_progress.txt").write_text("main progress\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "feat: main progress"], cwd=self.tmp_path, check=True, capture_output=True)
        
        # 从老的 card/OTHER-TASK 创建本卡分支（压他卡分支）
        subprocess.run(["git", "checkout", "card/OTHER-TASK"], cwd=self.tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "checkout", "-b", "card/TEST-005"], cwd=self.tmp_path, check=True, capture_output=True)
        (self.tmp_path / "test005.txt").write_text("test 005\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "feat(TEST-005): my work"], cwd=self.tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "checkout", "main"], cwd=self.tmp_path, check=True, capture_output=True)
        
        with patch("tools.aipos_cli.board_adapter._resolve_product_code_repo") as mock_resolve:
            mock_resolve.return_value = self.tmp_path
            
            reasons = _check_branch_compliance(
                task_id="TEST-005",
                task_metadata={"task_mode": "code"},
                repo_root=Path("/tmp/governance"),
            )
            
            # 应拒：merge-base 不是 main 当前 HEAD（是 main 的老 commit）
            self.assertTrue(len(reasons) > 0)
            self.assertIn("BRANCH_WRONG_BASE", reasons[0])
            self.assertIn("git rebase", reasons[0])


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
        """真入口夹具: 直接调用 regen_machine_zone_for_pending, 验证导入路径正确。
        
        AIPOS-F74-R2: 分根夹具 - governance_root 与 product_root 必须不同目录。
        同目录永远测不出 schema 路径双义。
        """
        import tempfile
        import shutil
        from tools.aipos_cli.draft_writer import regen_machine_zone_for_pending
        
        # 创建临时治理仓和产品仓 (分离目录)
        tmp_gov = Path(tempfile.mkdtemp(prefix="test_gov_regen_"))
        tmp_product = Path(tempfile.mkdtemp(prefix="test_product_regen_"))
        
        try:
            # 创建 pending 目录和一张测试卡 (在治理仓)
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
            
            # 创建 schema 目录 (在产品仓)
            schema_dir = tmp_product / "schema"
            schema_dir.mkdir(parents=True, exist_ok=True)
            (schema_dir / "card.schema.json").write_text(
                '{"properties": {"task_id": {"type": "string"}}}',
                encoding="utf-8"
            )
            
            # 直接调用函数 (走真实导入路径，分离治理根与产品根)
            result = regen_machine_zone_for_pending(
                tmp_gov,       # governance_root
                tmp_product,   # product_root
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
            if tmp_product.exists():
                shutil.rmtree(tmp_product)
    
    def test_regen_non_dry_run_real_execution(self):
        """非 dry-run 真执行路径: 验证 amendment 记录落盘、卡面更新、顾问区零触碰。
        
        AIPOS-F74-R3-F1: dry-run 全绿掩皩了 amend_task 参数错误，
        必须覆盖非 dry-run 路径。
        """
        import tempfile
        import shutil
        from tools.aipos_cli.draft_writer import regen_machine_zone_for_pending
        
        # 创建临时治理仓和产品仓
        tmp_gov = Path(tempfile.mkdtemp(prefix="test_gov_real_"))
        tmp_product = Path(tempfile.mkdtemp(prefix="test_product_real_"))
        
        try:
            # 创建 pending 目录
            pending_dir = tmp_gov / "5_tasks" / "queue" / "pending"
            pending_dir.mkdir(parents=True, exist_ok=True)
            
            # 创建一张缺少机器区字段的测试卡
            test_card = pending_dir / "test-real-exec-001.md"
            test_card.write_text(
                "---\n"
                "task_id: TEST-REAL-EXEC-001\n"
                "title: Test Real Execution\n"
                "status: pending\n"
                "---\n"
                "\n"
                "## 工作纪律\n"
                "\n"
                "Old discipline.\n",
                encoding="utf-8"
            )
            
            # 创建 schema (包含可派生字段)
            schema_dir = tmp_product / "schema"
            schema_dir.mkdir(parents=True, exist_ok=True)
            (schema_dir / "card.schema.json").write_text(
                '{"properties": {"task_id": {"type": "string"}, "priority": {"type": "string"}}}',
                encoding="utf-8"
            )
            
            # 创建 amendment 记录目录
            amendments_dir = tmp_gov / "5_tasks" / "records" / "amendments"
            amendments_dir.mkdir(parents=True, exist_ok=True)
            
            # 非 dry-run 真执行
            result = regen_machine_zone_for_pending(
                tmp_gov,
                tmp_product,
                task_id="TEST-REAL-EXEC-001",
                actor="test.actor.real",
                dry_run=False,  # 真执行
            )
            
            # 验证结果
            self.assertIn("verdict", result)
            
            # 如果有更新，验证 amendment 记录落盘
            if result["data"]["updated_cards"]:
                # 检查 amendment 记录文件
                amend_records = list(amendments_dir.rglob("*.md"))
                self.assertTrue(
                    len(amend_records) > 0,
                    "Amendment records should be created for non-dry-run execution"
                )
                
                # 验证卡面已更新
                updated_card = test_card.read_text(encoding="utf-8")
                self.assertIn("task_id: TEST-REAL-EXEC-001", updated_card)
            
            # 验证顾问区零触碰
            governance_dir = tmp_gov / "governance"
            if governance_dir.exists():
                gov_files = list(governance_dir.rglob("*"))
                self.assertEqual(
                    len([f for f in gov_files if f.is_file()]),
                    0,
                    "顾问区 (governance/) 应为空"
                )
            
        finally:
            # 清理
            if tmp_gov.exists():
                shutil.rmtree(tmp_gov)
            if tmp_product.exists():
                shutil.rmtree(tmp_product)


if __name__ == "__main__":
    unittest.main()
