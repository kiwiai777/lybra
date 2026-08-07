"""AIPOS-358 — auditor thin shell 单测。

验收目标:
1. 守护不自尽: subprocess 失败 → 继续循环, 不退出
2. CLI 参数面: --workspace-root 必填, --interval 默认 20
3. 零判定: thin shell 无任何"下一步/成败/卫生"判断逻辑
4. KeyboardInterrupt → 干净退出 130
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call


class ThinShellNeverExitsTests(unittest.TestCase):
    """守护不自尽: 业务失败不导致 daemon 退出。"""

    @patch("tools.aipos_cli.auditor_loop.time.sleep", side_effect=KeyboardInterrupt)
    @patch("tools.aipos_cli.auditor_loop.subprocess.run")
    def test_scan_failure_continues_loop(self, mock_run: MagicMock, mock_sleep: MagicMock) -> None:
        """scan 返回非零退出码 → daemon 继续(不退出)。"""
        from tools.aipos_cli.auditor_loop import run_daemon
        mock_run.return_value = MagicMock(returncode=1)
        ws = Path(tempfile.mkdtemp())
        rc = run_daemon(ws, interval=0.01)
        self.assertEqual(rc, 130)
        self.assertEqual(mock_run.call_count, 1)

    @patch("tools.aipos_cli.auditor_loop.time.sleep", side_effect=KeyboardInterrupt)
    @patch("tools.aipos_cli.auditor_loop.subprocess.run")
    def test_scan_exception_continues_loop(self, mock_run: MagicMock, mock_sleep: MagicMock) -> None:
        """scan 抛异常 → daemon 继续(不退出)。"""
        from tools.aipos_cli.auditor_loop import run_daemon
        mock_run.side_effect = RuntimeError("boom")
        ws = Path(tempfile.mkdtemp())
        rc = run_daemon(ws, interval=0.01)
        self.assertEqual(rc, 130)

    @patch("tools.aipos_cli.auditor_loop.time.sleep", side_effect=[None, KeyboardInterrupt])
    @patch("tools.aipos_cli.auditor_loop.subprocess.run")
    def test_scan_success_then_sleep_interrupt(self, mock_run: MagicMock, mock_sleep: MagicMock) -> None:
        """scan 成功 → sleep → 再次 scan → 中断。"""
        from tools.aipos_cli.auditor_loop import run_daemon
        mock_run.return_value = MagicMock(returncode=0)
        ws = Path(tempfile.mkdtemp())
        rc = run_daemon(ws, interval=0.01)
        self.assertEqual(rc, 130)
        self.assertEqual(mock_run.call_count, 2)


class ThinShellCLITests(unittest.TestCase):
    """CLI 参数面。"""

    def test_main_requires_workspace_root(self) -> None:
        """--workspace-root 缺失 → SystemExit(2)。"""
        from tools.aipos_cli.auditor_loop import main
        with self.assertRaises(SystemExit) as ctx:
            main([])
        self.assertEqual(ctx.exception.code, 2)

    def test_main_nonexistent_workspace(self) -> None:
        """不存在的 workspace → return 1。"""
        from tools.aipos_cli.auditor_loop import main
        rc = main(["--workspace-root", "/definitely/not/a/real/path/xyz358"])
        self.assertEqual(rc, 1)

    @patch("tools.aipos_cli.auditor_loop.run_daemon", return_value=130)
    def test_main_passes_interval(self, mock_daemon: MagicMock) -> None:
        """--interval 传递到 run_daemon。"""
        from tools.aipos_cli.auditor_loop import main
        with tempfile.TemporaryDirectory() as td:
            main(["--workspace-root", td, "--interval", "5.0"])
        mock_daemon.assert_called_once()
        self.assertAlmostEqual(mock_daemon.call_args[1].get("interval", mock_daemon.call_args[0][1] if len(mock_daemon.call_args[0]) > 1 else 20.0), 5.0, delta=0.1)


class ZeroDecisionTests(unittest.TestCase):
    """零判定断言: thin shell 不包含私有决策函数。"""

    def test_no_decision_functions_in_module(self) -> None:
        """旧决策函数不存在于 auditor_loop 模块。"""
        import tools.aipos_cli.auditor_loop as mod
        retired = [
            "find_pending_audit_cards",
            "check_reviewed_task_has_final_verdict",
            "resolve_reviewed_task_id",
            "check_verdict_landed",
            "process_pending_audits",
            "run_auditor_loop",
            "run_fs_watch",
            "write_skip_unresolvable_event",
            "write_skip_stale_card_event",
            "write_block_file",
            "write_verdict_missing_block",
            "write_audit_incomplete_event",
        ]
        for name in retired:
            self.assertFalse(
                hasattr(mod, name),
                f"Retired decision function '{name}' still exists in auditor_loop",
            )

    def test_launch_auditor_runtime_not_in_loop(self) -> None:
        """launch_auditor_runtime 已迁出 auditor_loop (到 auditor_runtime)。"""
        import tools.aipos_cli.auditor_loop as mod
        self.assertFalse(hasattr(mod, "launch_auditor_runtime"))


if __name__ == "__main__":
    unittest.main()
