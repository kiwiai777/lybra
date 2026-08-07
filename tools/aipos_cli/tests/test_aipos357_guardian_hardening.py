"""AIPOS-357 — 守护认卡健壮化测试 (交付 1/2/3a/4)。

覆盖四项交付:
1. 守护认卡前:被审卡 ID 解析失败(非 *R 或映射不出)→ skip_unresolvable, 不 claim 不啃。
2. skip 事件去重:同卡同类 skip 事件仅一次(治 320R 每轮重写之噪)。
3a. 守护 kickoff 模板事件落位给绝对工作区路径(源码护栏, 防 blocked_verdict_submit
    被写入产品仓根)。
4. 活体:构造无法解析的 audit 卡 → skip 事件一次且仅一次;320R 类账平卡多轮询零新增;
    守护空转稳定(无 pending 卡 → rc=0);零回归。

注:3b (lybra_task_progress 工作区根护栏) 的测试在 tools/mcp_server/tests/ 侧。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from tools.aipos_cli.auditor_loop import (
    process_pending_audits,
    resolve_reviewed_task_id,
    write_skip_stale_card_event,
    write_skip_unresolvable_event,
)


class _MockGateClient:
    """Mock gate client (mirrors test_auditor_loop.MockGateClient contract)."""

    def __init__(self, auto_released: bool = True) -> None:
        self.auto_released = auto_released
        self.call_count = 0

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.call_count += 1
        return {
            "preauthorized_release": self.auto_released,
            "autonomy_mode": "PreAuthorized" if self.auto_released else "Supervised",
            "verdict": "ALLOW" if self.auto_released else "BLOCK",
            "claim_id": "claim_test" if self.auto_released else "",
            "active_session_id": "session_test" if self.auto_released else "",
        }


def _landed_stub() -> dict[str, Any]:
    """launch_auditor_runtime return stub: verdict landed (normal path)."""
    return {
        "exit_code": 0,
        "verdict_check": {
            "landed": True,
            "reason": "verdict landed (test stub)",
            "verdict_files": [],
            "card_status": "completed",
        },
        "retry_exhausted": False,
    }


class ResolveReviewedTaskIdTests(unittest.TestCase):
    """交付 1: 被审卡 ID 解析 (resolve_reviewed_task_id)。"""

    def test_explicit_frontmatter_field_wins(self) -> None:
        self.assertEqual(resolve_reviewed_task_id("AIPOS-340R", "AIPOS-340"), "AIPOS-340")

    def test_derive_from_R_suffix_when_no_frontmatter(self) -> None:
        self.assertEqual(resolve_reviewed_task_id("AIPOS-340R", ""), "AIPOS-340")
        self.assertEqual(resolve_reviewed_task_id("AIPOS-340R", None), "AIPOS-340")

    def test_unresolvable_non_R_no_frontmatter(self) -> None:
        """AIPOS-346A 特型补审卡(无 R 后缀 + 无 reviewed_task_id)→ 解析失败("")。"""
        self.assertEqual(resolve_reviewed_task_id("AIPOS-346A", ""), "")
        self.assertEqual(resolve_reviewed_task_id("AIPOS-346A", None), "")
        self.assertEqual(resolve_reviewed_task_id("SOME-SPECIAL", ""), "")

    def test_bare_R_is_not_a_valid_audit_id(self) -> None:
        self.assertEqual(resolve_reviewed_task_id("R", ""), "")

    def test_whitespace_only_frontmatter_falls_back_to_R_suffix(self) -> None:
        self.assertEqual(resolve_reviewed_task_id("AIPOS-340R", "   "), "AIPOS-340")
        self.assertEqual(resolve_reviewed_task_id("AIPOS-346A", "   "), "")


class SkipEventDedupTests(unittest.TestCase):
    """交付 2: skip 事件去重(同卡同类仅一次)。"""

    def test_skip_unresolvable_written_once_on_repeat(self) -> None:
        ws = Path(tempfile.mkdtemp(prefix="aipos357_"))
        for _ in range(5):
            write_skip_unresolvable_event(ws, "AIPOS-346A", "")
        evdir = ws / "5_tasks" / "records" / "events" / "AIPOS-346A"
        files = list(evdir.glob("skip_unresolvable_*.md"))
        self.assertEqual(len(files), 1, "skip_unresolvable 仅一次(去重)")
        content = files[0].read_text(encoding="utf-8")
        self.assertIn("event_kind: skip_unresolvable", content)

    def test_skip_stale_card_written_once_on_repeat(self) -> None:
        """320R 类账平卡多轮轮询 → skip_stale_card 零新增(治每轮重写之噪)。"""
        ws = Path(tempfile.mkdtemp(prefix="aipos357_"))
        for _ in range(5):
            write_skip_stale_card_event(ws, "AIPOS-320R", "AIPOS-320", "already has final verdict")
        evdir = ws / "5_tasks" / "records" / "events" / "AIPOS-320R"
        files = list(evdir.glob("skip_stale_card_*.md"))
        self.assertEqual(len(files), 1, "skip_stale_card 去重(首个之后不再写)")


class ProcessPendingSkipUnresolvableTests(unittest.TestCase):
    """交付 1/4 活体: 不可解析审计卡 → skip, 不 claim 不啃。"""

    @patch("tools.aipos_cli.auditor_loop.find_pending_audit_cards")
    @patch("tools.aipos_cli.auditor_loop.launch_auditor_runtime")
    def test_unresolvable_card_skipped_no_claim(
        self, mock_launch: MagicMock, mock_find: MagicMock
    ) -> None:
        """AIPOS-346A 类: 解析失败 → skip_unresolvable 一次, claim 零次, runtime 零次。"""
        ws = Path(tempfile.mkdtemp(prefix="aipos357_"))
        product = Path(tempfile.mkdtemp(prefix="aipos357_product_"))
        mock_find.return_value = [
            {
                "task_id": "AIPOS-346A",
                "reviewed_task_id": "",  # 无 R 后缀 + 无 reviewed_task_id → 不可解析
                "path": "/path/to/346a.md",
            }
        ]
        mock_launch.return_value = _landed_stub()
        mock_client = _MockGateClient(auto_released=True)

        rc = process_pending_audits(
            ws, product, mock_client, "audit.test", "pol_test", "echo '{kickoff}'", 5  # type: ignore[arg-type]
        )

        self.assertEqual(rc, 0)
        self.assertEqual(mock_client.call_count, 0, "不可解析卡: 不 claim")
        self.assertEqual(mock_launch.call_count, 0, "不可解析卡: 不拉起 runtime")
        evdir = ws / "5_tasks" / "records" / "events" / "AIPOS-346A"
        files = list(evdir.glob("skip_unresolvable_*.md"))
        self.assertEqual(len(files), 1, "skip_unresolvable 事件一次且仅一次")


class StaleCardMultiRoundZeroNewEventsTests(unittest.TestCase):
    """交付 2/4 活体: 320R 类账平卡多轮轮询 → 零新增 skip 事件 + 不 claim。"""

    @patch("tools.aipos_cli.auditor_loop.find_pending_audit_cards")
    @patch("tools.aipos_cli.auditor_loop.launch_auditor_runtime")
    def test_stale_card_multi_round_zero_new_events(
        self, mock_launch: MagicMock, mock_find: MagicMock
    ) -> None:
        ws = Path(tempfile.mkdtemp(prefix="aipos357_"))
        product = Path(tempfile.mkdtemp(prefix="aipos357_product_"))
        reviewed = "AIPOS-320"
        # 构造一份终态裁决 → 守护应 skip_stale_card(不 claim)
        verdicts_dir = ws / "5_tasks" / "records" / "audit_verdicts" / reviewed
        verdicts_dir.mkdir(parents=True)
        (verdicts_dir / "verdict_20260801_120000.md").write_text(
            "---\nverdict_result: PASS\n---\n# v\n", encoding="utf-8"
        )
        mock_find.return_value = [
            {"task_id": "AIPOS-320R", "reviewed_task_id": reviewed, "path": "/card.md"}
        ]
        mock_launch.return_value = _landed_stub()
        mock_client = _MockGateClient(auto_released=True)

        for _ in range(4):  # 4 轮守护轮询
            rc = process_pending_audits(
                ws, product, mock_client, "audit.test", "pol_test", "echo '{kickoff}'", 5  # type: ignore[arg-type]
            )
            self.assertEqual(rc, 0)

        evdir = ws / "5_tasks" / "records" / "events" / "AIPOS-320R"
        files = list(evdir.glob("skip_stale_card_*.md"))
        self.assertEqual(len(files), 1, "多轮轮询零新增(首个之后不再写)")
        self.assertEqual(mock_client.call_count, 0, "陈卡不 claim")
        self.assertEqual(mock_launch.call_count, 0, "陈卡不拉起 runtime")


class GuardianIdleStableTests(unittest.TestCase):
    """交付 4: 守护空转稳定(无 pending 卡 → rc=0,不报错不阻塞)。"""

    @patch("tools.aipos_cli.auditor_loop.find_pending_audit_cards")
    @patch("tools.aipos_cli.auditor_loop.launch_auditor_runtime")
    def test_no_pending_cards_returns_clean(
        self, mock_launch: MagicMock, mock_find: MagicMock
    ) -> None:
        ws = Path(tempfile.mkdtemp(prefix="aipos357_"))
        product = Path(tempfile.mkdtemp(prefix="aipos357_product_"))
        mock_find.return_value = []  # 空转
        mock_client = _MockGateClient(auto_released=True)

        rc = process_pending_audits(
            ws, product, mock_client, "audit.test", "pol_test", "echo '{kickoff}'", 5  # type: ignore[arg-type]
        )

        self.assertEqual(rc, 0)
        self.assertEqual(mock_client.call_count, 0)
        self.assertEqual(mock_launch.call_count, 0)


class KickoffEventPathAbsoluteTests(unittest.TestCase):
    """交付 3a: 守护 kickoff 事件落位必须是绝对工作区路径(源码护栏)。

    防 blocked_verdict_submit 事件被审计 agent 按相对路径写入产品仓根(实证
    2026-08-07 的 S10 misdirect)。源码护栏与现有红线断言风格一致(扫模块源)。
    """

    def test_kickoff_uses_absolute_workspace_event_path(self) -> None:
        import tools.aipos_cli.auditor_loop as m

        source = Path(m.__file__).read_text(encoding="utf-8")  # type: ignore[arg-type]

        # 绝对路径变量从 workspace_root 派生
        self.assertIn(
            'workspace_root / "5_tasks" / "records" / "events" / audit_task_id',
            source,
            "events 路径必须从 workspace_root 派生(绝对)",
        )
        # kickoff f-string 引用绝对 glob, 而非裸相对路径
        self.assertIn("{events_abs_glob}", source)
        self.assertNotIn(
            "写 blocked 事件到 5_tasks/records/events/",
            source,
            "kickoff 不得引用裸相对事件路径(会被 agent 按产品仓 cwd misdirect)",
        )


if __name__ == "__main__":
    unittest.main()
