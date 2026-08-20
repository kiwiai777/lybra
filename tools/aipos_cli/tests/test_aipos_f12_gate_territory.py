"""AIPOS-F12 大项D: 派审注入硬话 + 精确提交配方(值来自声明)。

验收⑤: 派审生成的审计卡含硬话 + 提交配方两段, 且路径/参数值来自声明。
"""
from __future__ import annotations

import unittest
from pathlib import Path

from tools.aipos_cli.audit_derivation import (
    build_derived_audit_task,
    build_gate_territory_discipline_section,
)


class TestGateTerritoryDisciplineSection(unittest.TestCase):
    """门领地纪律 + 提交配方注入。"""

    def test_section_contains_hard_talk_and_recipe(self):
        section = build_gate_territory_discipline_section("AIPOS-X", None)
        # 硬话两段
        self.assertIn("records/ = 门领地", section)
        self.assertIn("审计报告草稿只能落", section)
        # 提交配方两段
        self.assertIn("精确提交配方", section)
        self.assertIn("lybra_audit_verdict_dry_run", section)
        self.assertIn("lybra_audit_verdict_confirm", section)
        self.assertIn("owner_confirmation_token", section)

    def test_recipe_verb_names_from_registry(self):
        """动词名派生自 gate 注册表(verb_contract), 禁写死 — 与注册表一致。"""
        from tools.aipos_cli.verb_contract import get_verb_names
        section = build_gate_territory_discipline_section("AIPOS-X", None)
        registered = get_verb_names()
        self.assertIn("lybra_audit_verdict_dry_run", registered)
        self.assertIn("lybra_audit_verdict_confirm", registered)
        # 注入文本里出现的这两个动词名必须仍在注册表(改名自动跟随的判据)
        for verb in ("lybra_audit_verdict_dry_run", "lybra_audit_verdict_confirm"):
            self.assertIn(verb, section)

    def test_derived_audit_card_contains_section(self):
        """build_derived_audit_task 的 body 含门领地纪律节。"""
        source_metadata = {
            "title": "Test",
            "project": "lybra",
            "task_mode": "code",
            "audit": "required",
            "priority": "high",
        }
        result = build_derived_audit_task(
            source_task_id="AIPOS-F12T",
            source_metadata=source_metadata,
            source_path="5_tasks/queue/claimed/aipos-f12t.md",
            return_record_ref="return_x",
            artifact_refs=[],
            repo_root=None,
        )
        self.assertIn("门领地纪律", result["body"])
        self.assertIn("精确提交配方", result["body"])


if __name__ == "__main__":
    unittest.main()
