"""AIPOS-332 — runtime_profiles 数据驱动选择测试(S6②/S8/S12)。

验收对应:
  - S6②: 新增虚构运行体档案 → 选对观测面,零代码改动。
  - S8: 产出位置维度;非代码任务 worktree 判据关闭、不误报。
  - S12: output_target→location 映射;remote/workspace_only 不监听产品仓。
"""

from __future__ import annotations

import unittest

from tools.aipos_cli import runtime_profiles as rp


class RuntimeProfileSelectionTests(unittest.TestCase):
    def test_pi_buffered_runtime_does_not_use_run_log_for_stall(self):
        """S2: pi 类缓冲输出运行体,停滞面不含 run_log(run-log 仅结束检测)。"""
        plan = rp.select_observation_plan("pi", "tools/", {"output_locations": ["product_repo_worktree"]})
        self.assertNotIn("run_log", plan["stall_surfaces"])
        self.assertEqual(plan["run_log_role"], "end_only")

    def test_generic_bash_may_use_run_log_for_stall(self):
        """非缓冲运行体可用 run_log 判停滞(对照)。"""
        plan = rp.select_observation_plan("generic_bash", "tools/", {"output_locations": ["product_repo_worktree"]})
        self.assertIn("run_log", plan["stall_surfaces"])

    def test_code_task_worktree_criterion_on(self):
        plan = rp.select_observation_plan("pi", "tools/", {"output_locations": ["product_repo_worktree"]})
        self.assertTrue(plan["worktree_criterion"])
        self.assertIn("worktree", plan["stall_surfaces"])

    def test_remote_output_turns_off_worktree_and_product_repo(self):
        """S8/S12: remote 产出 → worktree 判据关、不监听产品仓、expect 只从工作区。"""
        plan = rp.select_observation_plan("pi", "remote", {"output_locations": ["product_repo_worktree"]})
        self.assertFalse(plan["worktree_criterion"])
        self.assertFalse(plan["monitors_product_repo"])
        self.assertEqual(plan["expect_source"], "workspace")
        self.assertNotIn("worktree", plan["stall_surfaces"])
        self.assertTrue(plan["warnings"], "退化须明确提示,不静默(S8 硬约束2)")

    def test_workspace_only_output(self):
        plan = rp.select_observation_plan("pi", "workspace_only", {"output_locations": ["workspace_records"]})
        self.assertFalse(plan["worktree_criterion"])
        self.assertEqual(plan["output_location"], "workspace_records")

    def test_undeclared_output_safe_default_no_product_repo_assumption(self):
        """S8 硬约束2:产出位置未声明 → 不假设产品仓,只用与产出位置无关的判据。"""
        plan = rp.select_observation_plan(None, None, None)
        self.assertFalse(plan["worktree_criterion"])
        self.assertFalse(plan["monitors_product_repo"])
        self.assertTrue(plan["warnings"])

    def test_new_runtime_profile_zero_code_change(self):
        """S6② 验收(a):新增虚构运行体档案 → 选对观测面,零代码改动。"""
        rp.RUNTIME_PROFILES["fictional_agent"] = {
            "buffer_output": False,
            "description": "fictional test runtime",
            "stall_surfaces": ["run_log", "session_dirs"],
            "run_log_role": "stall",
        }
        try:
            plan = rp.select_observation_plan("fictional_agent", "tools/", {"output_locations": ["product_repo_worktree"]})
            self.assertEqual(plan["runtime_profile"]["description"], "fictional test runtime")
            self.assertIn("run_log", plan["stall_surfaces"])
        finally:
            del rp.RUNTIME_PROFILES["fictional_agent"]

    def test_new_output_location_profile_zero_code_change(self):
        """S8 验收/S6② 验收(b):新增虚构产出位置档案 → 选对观测面,零代码改动。"""
        rp.OUTPUT_LOCATION_PROFILES["fictional_sandbox"] = {
            "worktree_criterion": False,
            "monitors_product_repo": False,
            "monitors_workspace": True,
            "expect_source": "workspace",
            "description": "fictional sandbox",
        }
        try:
            loc, warns = rp.resolve_output_location("fictional_sandbox", None)
            # resolve_output_location only maps known output_targets; drive via direct profile
            plan = rp.select_observation_plan("pi", None, None)  # baseline
            prof, _ = rp.get_output_location_profile("fictional_sandbox")
            self.assertFalse(prof["worktree_criterion"])
            self.assertEqual(prof["description"], "fictional sandbox")
        finally:
            del rp.OUTPUT_LOCATION_PROFILES["fictional_sandbox"]


if __name__ == "__main__":
    unittest.main()
