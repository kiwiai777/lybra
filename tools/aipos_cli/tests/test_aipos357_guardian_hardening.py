"""AIPOS-358 — 审计守护薄壳化验收: 旧决策函数零残留 + 新组装验证。

原 AIPOS-357 守护硬化测试的决策函数已随 AIPOS-358 薄壳化退役。
本文件验证:
1. 旧决策函数在仓库中不存在 (grep 零残留)
2. 新组装: launch_auditor_runtime 在 auditor_runtime.py 中可用
3. claim_preauthorized 在 auditor_runtime.py 中可用
4. thin shell 模块只导出 log + run_daemon + main
"""
from __future__ import annotations

import unittest
from pathlib import Path


class ZeroResidualTests(unittest.TestCase):
    """验收断言 1: grep 零残留 — 旧审计守护私有决策函数不存在。"""

    def _check_function_gone(self, func_name: str) -> None:
        """Verify a function definition doesn't exist in the codebase (excluding .deploy)."""
        repo_root = Path(__file__).resolve().parents[4]  # lybra/
        for py_file in repo_root.rglob("*.py"):
            if ".deploy" in py_file.parts or "__pycache__" in py_file.parts:
                continue
            if py_file.name == "test_aipos357_guardian_hardening.py":
                continue  # skip this test file itself
            try:
                text = py_file.read_text(encoding="utf-8")
            except OSError:
                continue
            # Check for function definitions (def func_name)
            if f"def {func_name}(" in text:
                self.fail(
                    f"Retired function '{func_name}' still defined in {py_file}"
                )

    def test_find_pending_audit_cards_gone(self) -> None:
        self._check_function_gone("find_pending_audit_cards")

    def test_check_reviewed_task_has_final_verdict_gone(self) -> None:
        self._check_function_gone("check_reviewed_task_has_final_verdict")

    def test_resolve_reviewed_task_id_gone(self) -> None:
        self._check_function_gone("resolve_reviewed_task_id")

    def test_check_verdict_landed_gone(self) -> None:
        self._check_function_gone("check_verdict_landed")

    def test_process_pending_audits_gone(self) -> None:
        self._check_function_gone("process_pending_audits")

    def test_run_auditor_loop_gone(self) -> None:
        self._check_function_gone("run_auditor_loop")

    def test_write_skip_unresolvable_event_gone(self) -> None:
        self._check_function_gone("write_skip_unresolvable_event")

    def test_write_skip_stale_card_event_gone(self) -> None:
        self._check_function_gone("write_skip_stale_card_event")

    def test_write_block_file_gone_from_auditor_loop(self) -> None:
        """write_block_file 可能存在于 agent_launch_check.py, 但不在 auditor_loop.py。"""
        import tools.aipos_cli.auditor_loop as mod
        self.assertFalse(hasattr(mod, "write_block_file"))

    def test_write_verdict_missing_block_gone(self) -> None:
        self._check_function_gone("write_verdict_missing_block")

    def test_write_audit_incomplete_event_gone(self) -> None:
        self._check_function_gone("write_audit_incomplete_event")


class NewAssemblyTests(unittest.TestCase):
    """验收断言: 新组装正确 — 执行工具可导入。"""

    def test_launch_auditor_runtime_importable(self) -> None:
        """launch_auditor_runtime 在 auditor_runtime 中可用。"""
        from tools.aipos_cli.auditor_runtime import launch_auditor_runtime
        self.assertTrue(callable(launch_auditor_runtime))

    def test_claim_preauthorized_importable(self) -> None:
        """claim_preauthorized 在 auditor_runtime 中可用。"""
        from tools.aipos_cli.auditor_runtime import claim_preauthorized
        self.assertTrue(callable(claim_preauthorized))

    def test_auditor_loop_is_thin_shell(self) -> None:
        """auditor_loop 导出 run_daemon + main, 无私有决策函数。"""
        import tools.aipos_cli.auditor_loop as mod
        # Key functions exist
        self.assertTrue(hasattr(mod, "run_daemon") and callable(mod.run_daemon))
        self.assertTrue(hasattr(mod, "main") and callable(mod.main))
        self.assertTrue(hasattr(mod, "log") and callable(mod.log))
        # No decision functions (spot check the most critical ones)
        for name in ["find_pending_audit_cards", "process_pending_audits",
                     "run_auditor_loop", "check_verdict_landed"]:
            self.assertFalse(hasattr(mod, name), f"{name} should not exist in thin shell")


if __name__ == "__main__":
    unittest.main()
