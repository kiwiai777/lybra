"""AIPOS-332 — pump_orchestration 编排层测试(S1/S6③/S7/S9/S9b/S11)。

这些测试不拉起真实 agent/gate(判断留人);只验证编排的【派生与契约】逻辑,
以及单步可独立调用(S6① 验收 a)。
"""

from __future__ import annotations

import unittest
from pathlib import Path

from tools.aipos_cli import pump_orchestration as po
from tools.aipos_cli.advisor_pump import generate_kickoff


class S7KickoffExpansionTests(unittest.TestCase):
    def _ctx(self):
        return po.DispatchContext(
            card_id="AIPOS-T1", role="executor", round_type="first",
            workspace_root=Path("/ws"), product_repo=Path("/repo"),
            gate_url="http://g:7118", envelope="env-1",
        )

    def test_placeholders_expanded_no_residual(self):
        raw = generate_kickoff("AIPOS-T1", "executor", "first", "")
        ctx = self._ctx()
        out = po.step_expand_kickoff(ctx, raw)
        self.assertNotIn("{workspace}", out)
        self.assertNotIn("{gate}", out)
        self.assertNotIn("{product_repo}", out)
        self.assertNotIn("{envelope}", out)
        import re
        self.assertEqual(re.findall(r"\{[a-z_]+\}", out), [])

    def test_missing_value_raises_no_half_baked_output(self):
        raw = generate_kickoff("AIPOS-T1", "executor", "first", "")
        ctx = self._ctx()
        ctx.envelope = ""  # 缺信封
        with self.assertRaises(ValueError) as cm:
            po.step_expand_kickoff(ctx, raw)
        self.assertIn("envelope", str(cm.exception))

    def test_residual_unknown_placeholder_raises(self):
        ctx = self._ctx()
        with self.assertRaises(ValueError):
            po.step_expand_kickoff(ctx, "hello {unknown_field} world")


class S9ExpectDerivationTests(unittest.TestCase):
    def test_executor_return_lands_on_own_id(self):
        deriv = po.derive_expect_patterns(
            "AIPOS-T2", "executor",
            {"expect_source": "both", "monitors_product_repo": True},
        )
        patterns = [e["pattern"] for e in deriv["patterns"]]
        self.assertIn("5_tasks/records/returns/AIPOS-T2/*.md", patterns)

    def test_auditor_verdict_lands_on_reviewed_card_id(self):
        """S9 硬约束1:审计裁决落【被审卡】ID 目录(非审计卡 ID)—— 当日 P0 失效根因。"""
        deriv = po.derive_expect_patterns(
            "AIPOS-T2R", "auditor",
            {"expect_source": "both", "monitors_product_repo": True},
            reviewed_task_id="AIPOS-T2",
        )
        patterns = [e["pattern"] for e in deriv["patterns"]]
        self.assertIn("5_tasks/records/audit_verdicts/AIPOS-T2/*.md", patterns)
        self.assertNotIn("5_tasks/records/audit_verdicts/AIPOS-T2R/*.md", patterns)

    def test_blocked_signal_is_workspace_events_not_product_repo(self):
        """S9 硬约束1:BLOCK 信号是工作区 events/blocked_*,不是产品仓 task_cards。"""
        deriv = po.derive_expect_patterns(
            "AIPOS-T2", "executor", {"expect_source": "both", "monitors_product_repo": True}
        )
        patterns = [e["pattern"] for e in deriv["patterns"]]
        self.assertIn("5_tasks/records/events/AIPOS-T2/blocked_*.md", patterns)
        for p in patterns:
            self.assertNotIn("task_cards", p)

    def test_remote_does_not_derive_product_repo_paths(self):
        """S12 落实3:remote 只从工作区 events 派生。"""
        deriv = po.derive_expect_patterns(
            "AIPOS-T2", "executor",
            {"expect_source": "workspace", "monitors_product_repo": False},
        )
        self.assertFalse(deriv["monitors_product_repo"])
        self.assertTrue(deriv["note"])  # 有说明


class S9bPreExistingTests(unittest.TestCase):
    def test_pre_existing_match_is_flagged_with_label(self):
        """S9b 硬约束1:布防前已存在的命中须明确标注,不得与本轮新产出混为一谈。"""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            ret_dir = ws / "5_tasks/records/returns/AIPOS-T3"
            ret_dir.mkdir(parents=True)
            (ret_dir / "return_old.md").write_text("x", encoding="utf-8")
            patterns = [{"pattern": "5_tasks/records/returns/AIPOS-T3/*.md", "meaning": "return"}]
            v = po.verify_sentinel_params(ws, patterns, None, {"stall_surfaces": ["session_dirs"], "run_log_role": "end_only", "warnings": []})
            self.assertTrue(v["expect_status"][0]["matched"])
            self.assertTrue(v["expect_status"][0].get("pre_existing"))
            self.assertIn("布防前已存在", v["expect_status"][0]["label"])

    def test_no_pre_existing_when_empty(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            patterns = [{"pattern": "5_tasks/records/returns/AIPOS-T4/*.md", "meaning": "return"}]
            v = po.verify_sentinel_params(ws, patterns, None, {"stall_surfaces": ["session_dirs"], "run_log_role": "end_only", "warnings": []})
            self.assertFalse(v["expect_status"][0]["matched"])

    def test_stall_surface_conflict_detected(self):
        """S2 硬约束:缓冲运行体却用 run_log 判停滞 → 自证报错,不空等。"""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            patterns = [{"pattern": "5_tasks/records/returns/AIPOS-T5/*.md", "meaning": "return"}]
            v = po.verify_sentinel_params(ws, patterns, None, {"stall_surfaces": ["run_log"], "run_log_role": "end_only", "warnings": []})
            self.assertTrue(v["errors"])


class S3UnmanagedDetectionTests(unittest.TestCase):
    def test_started_without_close_is_unmanaged(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            ev = ws / "5_tasks/records/events/AIPOS-T6"
            ev.mkdir(parents=True)
            (ev / "started_1.md").write_text("x", encoding="utf-8")
            un = po.list_unmanaged_agents(Path(d), ws, managed_task_ids=set())
            ids = [u["task_id"] for u in un]
            self.assertIn("AIPOS-T6", ids)

    def test_completed_is_not_unmanaged(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            ev = ws / "5_tasks/records/events/AIPOS-T7"
            ev.mkdir(parents=True)
            (ev / "started_1.md").write_text("x", encoding="utf-8")
            (ev / "completed_1.md").write_text("x", encoding="utf-8")
            un = po.list_unmanaged_agents(Path(d), ws, managed_task_ids=set())
            ids = [u["task_id"] for u in un]
            self.assertNotIn("AIPOS-T7", ids)

    def test_managed_task_id_excluded(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            ev = ws / "5_tasks/records/events/AIPOS-T8"
            ev.mkdir(parents=True)
            (ev / "started_1.md").write_text("x", encoding="utf-8")
            un = po.list_unmanaged_agents(Path(d), ws, managed_task_ids={"AIPOS-T8"})
            ids = [u["task_id"] for u in un]
            self.assertNotIn("AIPOS-T8", ids)


if __name__ == "__main__":
    unittest.main()
