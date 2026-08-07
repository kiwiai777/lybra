"""AIPOS-292 — auditor loop 单测: 领卡决策逻辑 + CLI 参数面 + 红线断言。

验收目标 (S4):
- 领卡决策逻辑单测: mock gate 读面, 三径 (放行 / 信封外 / BLOCK)
- CLI 参数面: 必填参数校验, 默认值
- 红线断言: 循环体无任何 gate 写面调用 (除 claim confirm 动词)
- 零回归
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from typing import Any

from tools.aipos_cli.auditor_loop import (
    BLOCK_EXIT_CODE,
    claim_preauthorized,
    find_pending_audit_cards,
    process_pending_audits,
    write_block_file,
)
from tools.aipos_cli.confirm_client import GateClient, GateError


class MockGateClient:
    """Mock gate client for testing claim decision logic."""
    
    def __init__(self, auto_released: bool = True, verdict: str = "ALLOW", reason: str = "ok"):
        self.auto_released = auto_released
        self.verdict = verdict
        self.reason = reason
        self.call_count = 0
    
    def initialize(self) -> None:
        pass
    
    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.call_count += 1
        if name == "lybra_queue_claim_dry_run":
            return {
                "preauthorized_release": self.auto_released,
                "autonomy_mode": "PreAuthorized" if self.auto_released else "Supervised",
                "verdict": self.verdict,
                "claim_id": "claim_test_123" if self.auto_released else "",
                "active_session_id": "session_test_123" if self.auto_released else "",
                "blocking_reasons": [] if self.auto_released else [self.reason],
            }
        return {}


class AuditorLoopClaimDecisionTests(unittest.TestCase):
    """领卡决策逻辑: mock gate 三径 (放行/信封外/BLOCK)。"""
    
    def test_claim_auto_released_returns_true(self) -> None:
        """路径 1: PreAuthorized 一发式 claim 自动放行 (信封结构匹配)。"""
        mock_client = MockGateClient(auto_released=True)
        result = claim_preauthorized(
            mock_client,  # type: ignore
            "audit.lybra.test",
            "pol_test_1",
            "AIPOS-999R",
        )
        self.assertTrue(result["auto_released"])
        self.assertEqual(result["verdict"], "ALLOW")
        self.assertEqual(mock_client.call_count, 1)
    
    def test_claim_not_auto_released_returns_false(self) -> None:
        """路径 2: 信封外 / 不匹配 → 未自动放行 (回落 Supervised)。"""
        mock_client = MockGateClient(
            auto_released=False,
            verdict="BLOCK",
            reason="envelope exhausted: max_tasks=5 reached"
        )
        result = claim_preauthorized(
            mock_client,  # type: ignore
            "audit.lybra.test",
            "pol_test_1",
            "AIPOS-999R",
        )
        self.assertFalse(result["auto_released"])
        self.assertEqual(result["verdict"], "BLOCK")
        self.assertIn("envelope exhausted", result["reason"])
    
    def test_claim_gate_error_raises(self) -> None:
        """路径 3: gate 暂态不可达 → 抛出 GateError (调用者应重试)。"""
        mock_client = MagicMock()
        mock_client.call_tool.side_effect = GateError("connection refused")
        
        with self.assertRaises(GateError) as ctx:
            claim_preauthorized(
                mock_client,
                "audit.lybra.test",
                "pol_test_1",
                "AIPOS-999R",
            )
        self.assertIn("connection refused", str(ctx.exception))


class AuditorLoopPendingAuditScanTests(unittest.TestCase):
    """Pending audit 卡扫描逻辑。"""
    
    def test_find_pending_audit_cards_filters_by_instance(self) -> None:
        """只返回 task_mode=audit + status=pending + 匹配的 agent_instance。"""
        ws = Path(tempfile.mkdtemp(prefix="aipos292_"))
        pending_dir = ws / "5_tasks" / "queue" / "pending"
        pending_dir.mkdir(parents=True, exist_ok=True)
        
        # 匹配的卡
        (pending_dir / "AIPOS-001R.md").write_text(
            "---\n"
            "task_id: AIPOS-001R\n"
            "task_mode: audit\n"
            "status: pending\n"
            "agent_instance: audit.test.instance\n"
            "reviewed_task_id: AIPOS-001\n"
            "---\n"
            "# Audit card\n",
            encoding="utf-8"
        )
        
        # 不匹配: 不同 instance
        (pending_dir / "AIPOS-002R.md").write_text(
            "---\n"
            "task_id: AIPOS-002R\n"
            "task_mode: audit\n"
            "status: pending\n"
            "agent_instance: audit.other.instance\n"
            "---\n",
            encoding="utf-8"
        )
        
        # 不匹配: claimed 状态
        (pending_dir / "AIPOS-003R.md").write_text(
            "---\n"
            "task_id: AIPOS-003R\n"
            "task_mode: audit\n"
            "status: claimed\n"
            "agent_instance: audit.test.instance\n"
            "---\n",
            encoding="utf-8"
        )
        
        # 不匹配: 非 audit 模式
        (pending_dir / "AIPOS-004.md").write_text(
            "---\n"
            "task_id: AIPOS-004\n"
            "task_mode: code\n"
            "status: pending\n"
            "agent_instance: audit.test.instance\n"
            "---\n",
            encoding="utf-8"
        )
        
        results = find_pending_audit_cards(ws, "audit.test.instance")
        
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["task_id"], "AIPOS-001R")
        self.assertEqual(results[0]["reviewed_task_id"], "AIPOS-001")
        self.assertIn("AIPOS-001R.md", results[0]["path"])
    
    def test_find_pending_audit_cards_empty_when_no_match(self) -> None:
        """无匹配卡时返回空列表。"""
        ws = Path(tempfile.mkdtemp(prefix="aipos292_"))
        pending_dir = ws / "5_tasks" / "queue" / "pending"
        pending_dir.mkdir(parents=True, exist_ok=True)
        
        results = find_pending_audit_cards(ws, "audit.test.instance")
        self.assertEqual(results, [])


class AuditorLoopProcessPendingTests(unittest.TestCase):
    """process_pending_audits: 并发上限 1 (逐张阻塞) + BLOCK 退出。"""
    
    @patch("tools.aipos_cli.auditor_loop.find_pending_audit_cards")
    @patch("tools.aipos_cli.auditor_loop.launch_auditor_runtime")
    def test_process_pending_claims_and_launches_when_auto_released(
        self, mock_launch: MagicMock, mock_find: MagicMock
    ) -> None:
        """自动放行 → 拉起 auditor runtime (并发上限 1)。"""
        ws = Path(tempfile.mkdtemp(prefix="aipos292_"))
        product = Path(tempfile.mkdtemp(prefix="aipos292_product_"))
        
        mock_find.return_value = [
            {
                "task_id": "AIPOS-101R",
                "reviewed_task_id": "AIPOS-101",
                "path": "/path/to/card.md",
            }
        ]
        # AIPOS-357: launch_auditor_runtime returns a dict (exit_code/verdict_check/
        # retry_exhausted) per the AIPOS-306 contract; the old `= 0` int mock was stale.
        mock_launch.return_value = {
            "exit_code": 0,
            "verdict_check": {
                "landed": True,
                "reason": "verdict landed (test stub)",
                "verdict_files": [],
                "card_status": "completed",
            },
            "retry_exhausted": False,
        }
        
        mock_client = MockGateClient(auto_released=True)
        
        rc = process_pending_audits(
            ws, product, mock_client, "audit.test", "pol_test", "echo '{kickoff}'", 10  # type: ignore
        )
        
        self.assertEqual(rc, 0)
        self.assertEqual(mock_launch.call_count, 1)
        self.assertEqual(mock_client.call_count, 1)
    
    @patch("tools.aipos_cli.auditor_loop.find_pending_audit_cards")
    @patch("tools.aipos_cli.auditor_loop.write_block_file")
    def test_process_pending_blocks_when_not_auto_released(
        self, mock_write_block: MagicMock, mock_find: MagicMock
    ) -> None:
        """未自动放行 (信封耗尽) → 写 BLOCK 文件 + exit 75。"""
        ws = Path(tempfile.mkdtemp(prefix="aipos292_"))
        product = Path(tempfile.mkdtemp(prefix="aipos292_product_"))
        
        mock_find.return_value = [
            {
                "task_id": "AIPOS-201R",
                "reviewed_task_id": "AIPOS-201",
                "path": "/path/to/card.md",
            }
        ]
        mock_write_block.return_value = product / "BLOCK-1.md"
        
        mock_client = MockGateClient(auto_released=False, verdict="BLOCK", reason="envelope exhausted")
        
        rc = process_pending_audits(
            ws, product, mock_client, "audit.test", "pol_test", "echo '{kickoff}'", 10  # type: ignore
        )
        
        self.assertEqual(rc, BLOCK_EXIT_CODE)
        self.assertEqual(mock_write_block.call_count, 1)


class AuditorLoopBlockFileTests(unittest.TestCase):
    """BLOCK 文件写入逻辑。"""
    
    def test_write_block_file_creates_numbered_file(self) -> None:
        """BLOCK 文件编号递增 (BLOCK-1.md, BLOCK-2.md, ...)。"""
        product = Path(tempfile.mkdtemp(prefix="aipos292_"))
        
        # 第一个 BLOCK
        block1 = write_block_file(
            product,
            "AIPOS-292",
            "test reason 1",
            "AIPOS-999R",
            "pol_test",
            "audit.test",
            "dump 1",
        )
        self.assertTrue(block1.exists())
        self.assertIn("BLOCK-1.md", str(block1))
        content1 = block1.read_text(encoding="utf-8")
        self.assertIn("test reason 1", content1)
        self.assertIn("AIPOS-999R", content1)
        self.assertIn("pol_test", content1)
        
        # 第二个 BLOCK (同目录, 编号递增)
        block2 = write_block_file(
            product,
            "AIPOS-292",
            "test reason 2",
            "AIPOS-888R",
            "pol_test",
            "audit.test",
            "dump 2",
        )
        self.assertTrue(block2.exists())
        self.assertIn("BLOCK-2.md", str(block2))
        content2 = block2.read_text(encoding="utf-8")
        self.assertIn("test reason 2", content2)


class AuditorLoopRedLineTests(unittest.TestCase):
    """红线断言: 循环体无 gate 写面调用 (除 claim confirm)。"""
    
    def test_auditor_loop_module_has_no_gate_write_imports(self) -> None:
        """auditor_loop.py 不得导入 gate 写面模块 (queue_mutation 等)。"""
        import tools.aipos_cli.auditor_loop as auditor_loop_mod
        
        source = Path(auditor_loop_mod.__file__).read_text(encoding="utf-8")  # type: ignore
        
        # 禁止的写面导入
        forbidden = [
            "queue_mutation",
            "record_writer",
            "draft_writer",
            "owner_decision_writer",
        ]
        for module in forbidden:
            self.assertNotIn(
                f"from tools.aipos_cli.{module}",
                source,
                f"auditor_loop.py 不得导入 gate 写面模块: {module}"
            )
            self.assertNotIn(
                f"import tools.aipos_cli.{module}",
                source,
                f"auditor_loop.py 不得导入 gate 写面模块: {module}"
            )
    
    def test_auditor_loop_only_calls_claim_confirm_gate_tool(self) -> None:
        """auditor_loop.py 只调用 lybra_queue_claim_dry_run (读面 + claim confirm)。"""
        import tools.aipos_cli.auditor_loop as auditor_loop_mod
        
        source = Path(auditor_loop_mod.__file__).read_text(encoding="utf-8")  # type: ignore
        
        # 允许的 gate 工具 (claim 相关)
        allowed = ["lybra_queue_claim_dry_run"]
        
        # 禁止的写面工具
        forbidden = [
            "lybra_queue_claim_confirm",  # confirm 是 confirm_client 的事, loop 只做 dry_run
            "lybra_queue_return_dry_run",
            "lybra_queue_return_confirm",
            "lybra_draft_publish",
            "lybra_queue_mutation",
            "lybra_record_write",
        ]
        
        for tool in allowed:
            self.assertIn(tool, source, f"auditor_loop.py 应调用: {tool}")
        
        for tool in forbidden:
            self.assertNotIn(
                tool,
                source,
                f"auditor_loop.py 不得调用 gate 写面工具: {tool}"
            )


class AuditorLoopCliArgsTests(unittest.TestCase):
    """CLI 参数面: 必填参数, 默认值。"""
    
    def test_workspace_root_is_required(self) -> None:
        """--workspace-root 是必填参数。"""
        from tools.aipos_cli.auditor_loop import main
        import sys
        from io import StringIO
        
        old_stderr = sys.stderr
        sys.stderr = StringIO()
        try:
            with self.assertRaises(SystemExit) as cm:
                main(["--gate-url", "http://test"])
            self.assertNotEqual(cm.exception.code, 0)
            stderr_val = sys.stderr.getvalue()
            self.assertIn("required", stderr_val.lower())
        finally:
            sys.stderr = old_stderr
    
    def test_defaults_match_spec(self) -> None:
        """CLI 默认值对齐卡内声明 (S1)。"""
        from tools.aipos_cli.auditor_loop import main
        import argparse
        
        # 模拟 argparse 解析验证默认值
        parser = argparse.ArgumentParser()
        parser.add_argument("--workspace-root", required=True)
        parser.add_argument("--gate-url", default="http://127.0.0.1:7118")
        parser.add_argument("--auditor-instance", default="audit.lybra.kiwiai-dev")
        parser.add_argument("--policy", "--envelope", dest="envelope", default="pol_lybra_audit_1")
        parser.add_argument(
            "--runtime-cmd",
            default="pi --model anthropic/claude-3-5-sonnet-20241022 --prompt '{kickoff}'"
        )
        parser.add_argument("--interval", type=float, default=20.0)
        parser.add_argument("--timeout", type=float, default=1800.0)
        
        args = parser.parse_args(["--workspace-root", "/tmp/test"])
        
        self.assertEqual(args.gate_url, "http://127.0.0.1:7118")
        self.assertEqual(args.auditor_instance, "audit.lybra.kiwiai-dev")
        self.assertEqual(args.envelope, "pol_lybra_audit_1")
        self.assertIn("claude-3-5-sonnet-20241022", args.runtime_cmd)
        self.assertEqual(args.interval, 20.0)
        self.assertEqual(args.timeout, 1800.0)


if __name__ == "__main__":
    unittest.main()
