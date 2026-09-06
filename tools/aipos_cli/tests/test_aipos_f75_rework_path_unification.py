"""
AIPOS-F75 FAIL后修复通路单一化 —— 测试。

件①: 自动fix派生默认关闭 (manual模式不派生, auto模式才派生)
件②: 返工节进卡面 (queue_rework对claimed卡受限amend, 轮次上限, 禁触其他区)
件③: next联动读返工节 (显示未销账轮次的点杀清单)

跑法: python3 -m pytest tools/aipos_cli/tests/test_aipos_f75_rework_path_unification.py -v
"""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tools.aipos_cli.board_adapter import (
    audit_verdict_task,
    queue_rework_task,
    _read_fix_derivation_mode,
)
from tools.aipos_cli.frontmatter import parse_markdown_frontmatter
from tools.aipos_cli.next_resolver import derive_next_step
from tools.aipos_cli.queue_mutation import render_task_markdown
from tools.schema_constants import Verdict


class TestF75Part1FixDerivationSwitch(unittest.TestCase):
    """件①: FAIL裁决时读transitions.schema.json fix_derivation.mode开关"""

    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        self.schema_dir = self.repo_root / "schema"
        self.schema_dir.mkdir(parents=True, exist_ok=True)
        self.queue_dir = self.repo_root / "5_tasks" / "queue"
        self.claimed_dir = self.queue_dir / "claimed"
        self.claimed_dir.mkdir(parents=True, exist_ok=True)
        self.records_dir = self.repo_root / "5_tasks" / "records"
        (self.records_dir / "audit_verdicts").mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_transitions_schema(self, mode: str) -> None:
        """Helper: write transitions.schema.json with fix_derivation.mode"""
        schema = {
            "nodes": {
                "N4": {
                    "name": "audit_verdict",
                    "fix_derivation": {
                        "mode": mode,
                        "allowed_values": ["auto", "manual"],
                    }
                }
            }
        }
        schema_path = self.schema_dir / "transitions.schema.json"
        schema_path.write_text(json.dumps(schema, indent=2), encoding="utf-8")

    def _write_task_card(self, task_id: str, project: str = "lybra") -> None:
        """Helper: write a claimed task card"""
        metadata = {
            "task_id": task_id,
            "title": f"Test {task_id}",
            "project": project,
            "assigned_to": "executor_lybra",
            "agent_instance": "executor.lybra.kiwiai-dev",
            "context_bundle": "dev",
            "task_mode": "code",
            "task_class": "simple",
            "priority": "high",
            "status": "claimed",
            "created_by": "advisor",
            "needs_owner": False,
            "output_target": "",
            "artifact_policy": "formal_write",
        }
        filename = task_id.lower() + ".md"
        card_path = self.claimed_dir / filename
        card_path.write_text(render_task_markdown(metadata, "## Task body"), encoding="utf-8")

    def _write_audit_card(self, audit_id: str, reviewed_id: str) -> None:
        """Helper: write an audit (R) card"""
        metadata = {
            "task_id": audit_id,
            "title": f"Audit {reviewed_id}",
            "project": "lybra",
            "assigned_to": "auditor_lybra",
            "agent_instance": "auditor.lybra.kiwiai-dev",
            "context_bundle": "dev",
            "task_mode": "audit",
            "task_class": "simple",
            "priority": "high",
            "status": "claimed",
            "created_by": "advisor",
            "parent_task_id": reviewed_id,
            "needs_owner": False,
            "output_target": "",
            "artifact_policy": "formal_write",
        }
        filename = audit_id.lower() + ".md"
        card_path = self.claimed_dir / filename
        card_path.write_text(render_task_markdown(metadata, "## Audit body"), encoding="utf-8")

    def test_manual_mode_no_derivation(self) -> None:
        """先红: manual模式下FAIL裁决不派生*-fix1卡"""
        self._write_transitions_schema("manual")
        self._write_task_card("AIPOS-F75T1")
        self._write_audit_card("AIPOS-F75T1R", "AIPOS-F75T1")

        response = audit_verdict_task(
            audit_task_id="AIPOS-F75T1R",
            reviewed_task_id="AIPOS-F75T1",
            actor="auditor.lybra.kiwiai-dev",
            agent_instance="auditor.lybra.kiwiai-dev",
            owner_policy_ref="lybra_dev_policy",
            verdict="FAIL",
            findings_summary="test fail",
            dry_run=False,
            repo_root=self.repo_root,
        )

        # 验证: 无派生修复卡
        self.assertNotIn("auto_derived_repair_card", response.get("data", {}))
        # 验证: 有跳过提示
        self.assertIn("fix_derivation_skipped", response.get("data", {}))
        skipped = response["data"]["fix_derivation_skipped"]
        self.assertEqual(skipped["mode"], "manual")

        # 验证: pending目录无*-fix1卡
        pending_dir = self.queue_dir / "pending"
        if pending_dir.exists():
            fix_cards = list(pending_dir.glob("*-fix*.md"))
            self.assertEqual(len(fix_cards), 0, f"manual模式下不应派生fix卡, 但发现: {fix_cards}")

    def test_auto_mode_with_derivation(self) -> None:
        """后绿: auto模式下FAIL裁决才派生*-fix1卡"""
        self._write_transitions_schema("auto")
        self._write_task_card("AIPOS-F75T2")
        self._write_audit_card("AIPOS-F75T2R", "AIPOS-F75T2")

        response = audit_verdict_task(
            audit_task_id="AIPOS-F75T2R",
            reviewed_task_id="AIPOS-F75T2",
            actor="auditor.lybra.kiwiai-dev",
            agent_instance="auditor.lybra.kiwiai-dev",
            owner_policy_ref="lybra_dev_policy",
            verdict="FAIL",
            findings_summary="test fail",
            dry_run=False,
            repo_root=self.repo_root,
        )

        # 验证: 有派生修复卡
        self.assertIn("auto_derived_repair_card", response.get("data", {}))
        repair_info = response["data"]["auto_derived_repair_card"]
        self.assertTrue(repair_info.get("derived"))
        self.assertIn("fix1", repair_info.get("repair_task_id", "").lower())

    def test_read_fix_derivation_mode_default_manual(self) -> None:
        """读取开关: 无schema文件时默认manual"""
        mode = _read_fix_derivation_mode(self.repo_root)
        self.assertEqual(mode, "manual")

    def test_read_fix_derivation_mode_from_schema(self) -> None:
        """读取开关: 从schema正确读取"""
        self._write_transitions_schema("auto")
        mode = _read_fix_derivation_mode(self.repo_root)
        self.assertEqual(mode, "auto")


class TestF75Part2QueueReworkRestrictedAmend(unittest.TestCase):
    """件②: queue_rework对claimed卡受限amend, 只能改rework_rounds"""

    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        self.schema_dir = self.repo_root / "schema"
        self.schema_dir.mkdir(parents=True, exist_ok=True)
        self.queue_dir = self.repo_root / "5_tasks" / "queue"
        self.claimed_dir = self.queue_dir / "claimed"
        self.pending_dir = self.queue_dir / "pending"
        self.claimed_dir.mkdir(parents=True, exist_ok=True)
        self.pending_dir.mkdir(parents=True, exist_ok=True)
        self.amendments_dir = self.repo_root / "5_tasks" / "records" / "amendments"
        self.amendments_dir.mkdir(parents=True, exist_ok=True)
        self._write_transitions_schema()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_transitions_schema(self) -> None:
        """Helper: write transitions.schema.json with rework_limit"""
        schema = {
            "nodes": {
                "queue_rework": {
                    "guards": {
                        "rework_limit": {
                            "max_rounds": 2
                        }
                    }
                }
            }
        }
        schema_path = self.schema_dir / "transitions.schema.json"
        schema_path.write_text(json.dumps(schema, indent=2), encoding="utf-8")

    def _write_task_card(self, task_id: str, rework_rounds: list = None) -> Path:
        """Helper: write a claimed task card"""
        metadata = {
            "task_id": task_id,
            "title": f"Test {task_id}",
            "project": "lybra",
            "assigned_to": "executor_lybra",
            "agent_instance": "executor.lybra.kiwiai-dev",
            "context_bundle": "dev",
            "task_mode": "code",
            "task_class": "simple",
            "priority": "high",
            "status": "claimed",
            "created_by": "advisor",
            "needs_owner": False,
            "output_target": "",
            "artifact_policy": "formal_write",
        }
        if rework_rounds:
            metadata["rework_rounds"] = rework_rounds
        filename = task_id.lower() + ".md"
        card_path = self.claimed_dir / filename
        card_path.write_text(render_task_markdown(metadata, "## Task body"), encoding="utf-8")
        return card_path

    def test_rework_add_round_to_claimed_success(self) -> None:
        """对claimed卡追加返工节成功"""
        task_id = "AIPOS-F75T3"
        self._write_task_card(task_id)

        response = queue_rework_task(
            task_id=task_id,
            verdict_ref="verdict_123",
            focus_items=["Fix bug A", "Add test B"],
            acceptance_criteria="All tests pass",
            actor="advisor.lybra.kiwiai-dev",
            agent_instance="advisor.lybra.kiwiai-dev",
            owner_policy_ref="lybra_dev_policy",
            dry_run=False,
            repo_root=self.repo_root,
        )

        # 验证: 操作成功
        self.assertTrue(response.get("ok"))
        self.assertEqual(response["data"]["round_added"], 1)
        self.assertEqual(response["data"]["total_rounds"], 1)

        # 验证: 卡文件已更新
        card_path = self.claimed_dir / f"{task_id.lower()}.md"
        content = card_path.read_text(encoding="utf-8")
        metadata, _, _ = parse_markdown_frontmatter(content)
        self.assertIn("rework_rounds", metadata)
        self.assertEqual(len(metadata["rework_rounds"]), 1)
        self.assertEqual(metadata["rework_rounds"][0]["round"], 1)
        self.assertEqual(metadata["rework_rounds"][0]["verdict_ref"], "verdict_123")
        self.assertEqual(metadata["rework_rounds"][0]["focus_items"], ["Fix bug A", "Add test B"])

        # 验证: amendment记录已写入
        amendment_files = list((self.amendments_dir / task_id).glob("*.md"))
        self.assertGreater(len(amendment_files), 0, "应该有amendment记录")

    def test_rework_reject_pending_card(self) -> None:
        """对pending卡追加返工节被拒"""
        task_id = "AIPOS-F75T4"
        metadata = {
            "task_id": task_id,
            "title": f"Test {task_id}",
            "project": "lybra",
            "status": "pending",
            "assigned_to": "executor_lybra",
            "agent_instance": "executor.lybra.kiwiai-dev",
            "context_bundle": "dev",
            "task_mode": "code",
            "task_class": "simple",
            "priority": "high",
            "created_by": "advisor",
            "needs_owner": False,
            "output_target": "",
            "artifact_policy": "formal_write",
        }
        filename = task_id.lower() + ".md"
        card_path = self.pending_dir / filename
        card_path.write_text(render_task_markdown(metadata, "## Task body"), encoding="utf-8")

        response = queue_rework_task(
            task_id=task_id,
            verdict_ref="verdict_456",
            focus_items=["Fix C"],
            actor="advisor.lybra.kiwiai-dev",
            agent_instance="advisor.lybra.kiwiai-dev",
            owner_policy_ref="lybra_dev_policy",
            dry_run=True,
            repo_root=self.repo_root,
        )

        # 验证: 被BLOCK
        self.assertEqual(response.get("verdict"), Verdict.BLOCK)
        self.assertIn("NOT_CLAIMED", str(response.get("blocking_reasons", [])).upper())

    def test_rework_max_rounds_limit(self) -> None:
        """返工轮次超限(第3轮)被拒"""
        task_id = "AIPOS-F75T5"
        existing_rounds = [
            {"round": 1, "verdict_ref": "v1", "created_at": "2026-01-01T00:00:00Z", "created_by": "advisor", "focus_items": ["item1"]},
            {"round": 2, "verdict_ref": "v2", "created_at": "2026-01-02T00:00:00Z", "created_by": "advisor", "focus_items": ["item2"]},
        ]
        self._write_task_card(task_id, existing_rounds)

        response = queue_rework_task(
            task_id=task_id,
            verdict_ref="verdict_789",
            focus_items=["Fix D"],
            actor="advisor.lybra.kiwiai-dev",
            agent_instance="advisor.lybra.kiwiai-dev",
            owner_policy_ref="lybra_dev_policy",
            dry_run=True,
            repo_root=self.repo_root,
        )

        # 验证: 被BLOCK
        self.assertEqual(response.get("verdict"), Verdict.BLOCK)
        blocking = str(response.get("blocking_reasons", []))
        self.assertIn("上限", blocking)
        self.assertIn("2", blocking)


class TestF75Part3NextReadsReworkRounds(unittest.TestCase):
    """件③: next联动读返工节, 显示未销账轮次的点杀清单"""

    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.workspace_root = Path(self.temp_dir.name)
        self.queue_dir = self.workspace_root / "5_tasks" / "queue"
        self.claimed_dir = self.queue_dir / "claimed"
        self.claimed_dir.mkdir(parents=True, exist_ok=True)
        self.records_dir = self.workspace_root / "5_tasks" / "records"
        self.records_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_task_card(self, task_id: str, rework_rounds: list = None) -> None:
        """Helper: write a claimed task card"""
        metadata = {
            "task_id": task_id,
            "title": f"Test {task_id}",
            "project": "lybra",
            "assigned_to": "executor_lybra",
            "agent_instance": "executor.lybra.kiwiai-dev",
            "status": "claimed",
            "claimed_by": "executor",
            "claimed_at": "2026-01-01T00:00:00Z",
        }
        if rework_rounds:
            metadata["rework_rounds"] = rework_rounds
        filename = task_id.lower() + ".md"
        card_path = self.claimed_dir / filename
        card_path.write_text(render_task_markdown(metadata, "## Task body"), encoding="utf-8")

    def test_next_shows_uncleared_rework_round(self) -> None:
        """next对带未销账返工节的claimed卡显示点杀清单"""
        task_id = "AIPOS-F75T6"
        rework_rounds = [
            {
                "round": 1,
                "verdict_ref": "verdict_abc",
                "created_at": "2026-01-01T00:00:00Z",
                "created_by": "advisor",
                "focus_items": ["修复bug X", "添加测试 Y"],
                "acceptance_criteria": "所有测试通过",
            }
        ]
        self._write_task_card(task_id, rework_rounds)

        result = derive_next_step(task_id, self.workspace_root)

        # 验证: derivable=True
        self.assertTrue(result.get("derivable"))
        # 验证: notes包含点杀清单
        notes = result.get("notes", "")
        self.assertIn("返工节", notes)
        self.assertIn("修复bug X", notes)
        self.assertIn("添加测试 Y", notes)
        # 验证: rework_round字段存在
        self.assertIn("rework_round", result)
        self.assertEqual(result["rework_round"]["round"], 1)

    def test_next_skips_cleared_rework_round(self) -> None:
        """next对已销账的返工节不显示(走原逻辑)"""
        task_id = "AIPOS-F75T7"
        rework_rounds = [
            {
                "round": 1,
                "verdict_ref": "verdict_def",
                "created_at": "2026-01-01T00:00:00Z",
                "created_by": "advisor",
                "focus_items": ["修复bug Z"],
                "cleared_at": "2026-01-02T00:00:00Z",
                "cleared_by": "auditor",
            }
        ]
        self._write_task_card(task_id, rework_rounds)

        result = derive_next_step(task_id, self.workspace_root)

        # 验证: 不显示返工节(因为已销账)
        notes = result.get("notes", "")
        self.assertNotIn("返工节", notes)
        self.assertNotIn("修复bug Z", notes)


if __name__ == "__main__":
    unittest.main()
