"""AIPOS-SMOKE-LOOP-1 回归:登记处 08-12 两坑(dogfood 逮到)。

坑① audit-verdict CLI --audit-task-id 标可选但 gate 必填 → 改自动派生自 reviewed_task_id。
坑② task-progress CLI 报 ok:True 但 session 记录未落盘 → 改真落盘 + 禁吞错。

HAZARD-LEDGER (governance) 08-12 行 11/12, 本卡修复。
"""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tools.aipos_cli.audit_helpers import derive_audit_task_id
from tools.aipos_cli.task_progress_writer import write_task_progress_event


def _make_workspace(root: Path) -> Path:
    """Minimal Lybra governance workspace layout."""
    for sub in ("5_tasks/queue/pending", "5_tasks/queue/claimed",
                "5_tasks/records/events", "5_tasks/records/sessions",
                "5_tasks/records/claims", "schema"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def _write_claimed_card(root: Path, task_id: str, session_id: str, claim_id: str) -> Path:
    card = root / "5_tasks" / "queue" / "claimed" / f"{task_id.lower()}.md"
    card.write_text(
        "---\n"
        f"task_id: {task_id}\n"
        "status: claimed\n"
        f"active_session_id: {session_id}\n"
        f"claimed_by: exec.lybra.kiwiai-dev\n"
        f"claim_id: {claim_id}\n"
        "---\n# task\n",
        encoding="utf-8",
    )
    return card


def _write_session_record(root: Path, task_id: str, session_id: str, claim_id: str) -> Path:
    from tools.aipos_cli.record_writer import build_mcp_claim_session_record_markdown
    md = build_mcp_claim_session_record_markdown(
        task_id=task_id,
        task_path=f"5_tasks/queue/claimed/{task_id.lower()}.md",
        actor="exec.lybra.kiwiai-dev",
        canonical_agent_instance="exec.lybra.kiwiai-dev",
        owner_policy_ref="pol_lybra_dev_8",
        session_id=session_id,
        claim_id=claim_id,
        created_at="2026-08-12T10:00:00Z",
    )
    path = root / "5_tasks" / "records" / "sessions" / task_id / f"{session_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(md, encoding="utf-8")
    return path


class TestAuditTaskIdDerivation(unittest.TestCase):
    """坑①: --audit-task-id 自动派生自 reviewed_task_id。"""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = _make_workspace(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def _add_r_card(self, reviewed: str, audit_tid: str):
        card = self.root / "5_tasks" / "queue" / "claimed" / f"{audit_tid.lower()}.md"
        card.write_text(
            "---\n"
            f"task_id: {audit_tid}\n"
            f"derived_from: {reviewed}\n"
            "task_mode: audit\n"
            "---\n# audit\n",
            encoding="utf-8",
        )

    def test_derives_via_derived_from_field(self):
        self._add_r_card("AIPOS-344", "AIPOS-344R")
        self.assertEqual(derive_audit_task_id("AIPOS-344", self.root), "AIPOS-344R")

    def test_derives_via_naming_convention(self):
        # R card named {reviewed}R1 without derived_from
        card = self.root / "5_tasks" / "queue" / "claimed" / "aipos-777r1.md"
        card.write_text(f"---\ntask_id: AIPOS-777R1\n---\n# audit\n", encoding="utf-8")
        self.assertEqual(derive_audit_task_id("AIPOS-777", self.root), "AIPOS-777R1")

    def test_returns_none_when_no_r_card(self):
        self.assertIsNone(derive_audit_task_id("AIPOS-99999", self.root))

    def test_returns_none_on_ambiguous(self):
        self._add_r_card("AIPOS-500", "AIPOS-500R")
        card = self.root / "5_tasks" / "queue" / "claimed" / "aipos-500r2.md"
        card.write_text("---\ntask_id: AIPOS-500R2\nderived_from: AIPOS-500\n---\n# audit\n", encoding="utf-8")
        self.assertIsNone(derive_audit_task_id("AIPOS-500", self.root))


class TestTaskProgressSessionLanding(unittest.TestCase):
    """坑②: task-progress started 真落盘到 session record。"""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = _make_workspace(Path(self.tmp.name))
        self.task_id = "AIPOS-SMOKE-X"
        self.session_id = "session_smoke_x"
        self.claim_id = "claim_smoke_x"
        _write_claimed_card(self.root, self.task_id, self.session_id, self.claim_id)
        self.session_path = _write_session_record(
            self.root, self.task_id, self.session_id, self.claim_id
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_started_appends_event_and_updates_session(self):
        result = write_task_progress_event(
            repo_root=self.root,
            task_id=self.task_id,
            actor="exec.lybra.kiwiai-dev",
            event_type="started",
            agent_instance="exec.lybra.kiwiai-dev",
            summary="started work",
        )
        self.assertTrue(result["ok"], f"expected ok, got: {result}")
        self.assertTrue(result["recorded"], "recorded must be True")
        self.assertEqual(result["session_update"]["event_count"], 2)
        self.assertEqual(result["session_update"]["session_status"], "active")
        # session record file actually updated
        text = self.session_path.read_text(encoding="utf-8")
        self.assertIn("task_progress:started", text)
        self.assertIn("started work", text)

    def test_completed_updates_session_status(self):
        write_task_progress_event(
            repo_root=self.root, task_id=self.task_id,
            actor="a", event_type="started",
        )
        result = write_task_progress_event(
            repo_root=self.root, task_id=self.task_id,
            actor="a", event_type="completed",
            summary="done",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["session_update"]["session_status"], "completed")
        self.assertEqual(result["session_update"]["event_count"], 3)

    def test_blocks_loudly_when_no_session(self):
        """session 不存在 = 响亮报错,禁 ok:True 实没写 (HAZARD 08-12 行12 红线)。"""
        result = write_task_progress_event(
            repo_root=self.root,
            task_id="AIPOS-NO-SESSION",
            actor="a",
            event_type="started",
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["verdict"], "BLOCK")
        self.assertFalse(result["recorded"])
        self.assertTrue(result["blocking_reasons"], "must have a loud blocking reason")


if __name__ == "__main__":
    unittest.main()
